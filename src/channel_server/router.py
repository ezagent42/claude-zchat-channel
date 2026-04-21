"""中枢路由：IRC ↔ WS 双向翻译 + 命令分派。"""

from __future__ import annotations
import logging
import uuid
from typing import Any

from zchat_protocol import irc_encoding, ws_messages

from .plugin import PluginRegistry
from .routing import RoutingTable

log = logging.getLogger(__name__)


# IRC PRIVMSG 单条硬上限 512 字节（含协议头）；给 payload 留 ~200 字节文本空间
_IRC_SYS_TEXT_BYTES_LIMIT = 200
_IRC_SYS_TEXT_FIELDS = ("text", "content", "message")


def _slim_for_irc(data: dict) -> dict:
    """返回 data 的浅 copy，其中长文本字段按 UTF-8 字节截断，避免 IRC 超长。"""
    if not data:
        return {}
    out = dict(data)
    for key in _IRC_SYS_TEXT_FIELDS:
        v = out.get(key)
        if not isinstance(v, str):
            continue
        encoded = v.encode("utf-8")
        if len(encoded) <= _IRC_SYS_TEXT_BYTES_LIMIT:
            continue
        clipped = encoded[:_IRC_SYS_TEXT_BYTES_LIMIT].decode("utf-8", errors="ignore")
        out[key] = clipped + "…"
    return out


class Router:
    """核心路由。

    forward_inbound_ws: bridge → IRC
    forward_inbound_irc: IRC → bridges + plugins
    """

    DEFAULT_MODE = "copilot"  # 没有 mode plugin 时的默认

    def __init__(
        self,
        routing: RoutingTable,
        registry: PluginRegistry,
        irc_conn: Any,    # IRCConnection
        ws_server: Any,   # WSServer
    ) -> None:
        self._routing = routing
        self._registry = registry
        self._irc = irc_conn
        self._ws = ws_server

    # -------- inbound from WS (bridge → IRC) --------

    async def forward_inbound_ws(self, msg: dict) -> None:
        """处理从 bridge 来的 WS 消息。"""
        msg_type = msg["type"]

        if msg_type == ws_messages.WSType.MESSAGE:
            await self._handle_message(msg)
        elif msg_type == ws_messages.WSType.EVENT:
            # 让 plugin 订阅
            await self._registry.broadcast_event(msg)
            # 转发给其它 bridge（supervise 场景需要：customer bridge emit chat_info
            # → squad bridge 接收并缓存 chat_name）。sender bridge 会收到自己的
            # event 但按 own/supervised 检查过滤。
            await self._ws.broadcast(msg)
        # REGISTER 在 ws_server 层处理

    async def _handle_message(self, msg: dict) -> None:
        """处理 bridge 发来的 content 消息。

        1. 如果 content 以 "/" 开头且 plugin 接管 → 派给 plugin，**不**转 IRC
        2. 否则：查 mode 决定是否 @ prefix → IRC PRIVMSG
        3. 同时让所有 plugin 订阅
        """
        content = msg.get("content", "")
        channel = msg.get("channel", "")

        # 命令分派
        if content.startswith("/"):
            cmd_name = content[1:].split(maxsplit=1)[0]
            handler = self._registry.get_handler(cmd_name)
            if handler is not None:
                try:
                    await handler.on_command(cmd_name, msg)
                except Exception:
                    log.exception("[router] plugin %s on_command error", handler.name)
                # plugin 消费命令；不转发 IRC，但仍广播给订阅者
                await self._registry.broadcast_message(msg)
                return

        # 普通消息路由 → IRC
        await self._route_to_irc(channel, content, msg)
        # 同时广播给 plugins
        await self._registry.broadcast_message(msg)

    async def _route_to_irc(self, channel: str, content: str, msg: dict) -> None:
        """根据 mode 决定 @ prefix，发 IRC PRIVMSG。"""
        if not channel:
            return

        # 查 mode（从 mode plugin）
        mode = self._query_mode(channel)

        # 生成 IRC 频道名
        irc_channel = channel if channel.startswith("#") else f"#{channel}"

        # 包装成带 __msg: 前缀（如果 content 本身没有前缀）
        parsed = irc_encoding.parse(content)
        if parsed["kind"] == "plain":
            # 加 __msg: 前缀 + uuid
            mid = msg.get("message_id") or str(uuid.uuid4())
            encoded = irc_encoding.encode_msg(mid, content)
        else:
            # 已有前缀（bridge 转发的 thread reply 已自带 __side: 等）
            encoded = content

        # Mode 决定是否 @ prefix
        if mode in ("auto", "copilot"):
            entry = self._routing.entry_agent(channel)
            if not entry:
                log.warning(
                    "[router] channel %r has no entry_agent; emit help_requested",
                    channel,
                )
                await self.emit_event(channel, "help_requested",
                                       {"reason": "no_entry_agent"})
                return

            # 熔断：entry agent 不在 IRC channel 里 → emit help_requested 而非空 @
            # 只在 NAMES 缓存已 populated（非空）且 entry 不在的情况下熔断；
            # 空缓存 = 启动期还没收到 NAMES reply，fail open（允许 @，避免假阳性）。
            members = self._irc.names(irc_channel) if hasattr(self._irc, "names") else None
            if members and entry not in members:
                log.warning(
                    "[router] channel %r entry %r not in IRC NAMES; emit help_requested",
                    channel, entry,
                )
                await self.emit_event(channel, "help_requested",
                                       {"reason": "entry_offline", "entry": entry})
                return

            try:
                self._irc.privmsg(irc_channel, f"@{entry} {encoded}")
                log.info("[router] → IRC %s: @%s %s", irc_channel, entry, encoded[:60])
            except Exception:
                log.exception("[router] irc privmsg failed")
        else:
            # takeover: 不 @，消息直接到 IRC channel（agent 不会收到）
            try:
                self._irc.privmsg(irc_channel, encoded)
            except Exception:
                log.exception("[router] irc privmsg failed")

    def update_routing(self, new_routing: "RoutingTable") -> None:
        """热更新路由表（被 routing watcher 调用）。"""
        self._routing = new_routing

    @property
    def routing(self) -> "RoutingTable":
        return self._routing

    def _query_mode(self, channel: str) -> str:
        """通过 mode plugin 查当前 mode。无 plugin 或无记录返回默认。"""
        mode_plugin = self._registry.get_plugin("mode")
        if mode_plugin is None:
            return self.DEFAULT_MODE
        result = mode_plugin.query("get", {"channel": channel})
        return result or self.DEFAULT_MODE

    # -------- inbound from IRC (agent → bridges) --------

    async def forward_inbound_irc(self, irc_channel: str, nick: str, body: str) -> None:
        """IRC pubmsg → WS message → 广播 bridges + 命令分派。"""
        channel = irc_channel.lstrip("#")

        # 解析前缀（可能 agent 发了 __msg:/__edit:/__side: 等）
        parsed = irc_encoding.parse(body)
        text = parsed.get("text", body)

        # "/" 命令分派（agent 也能触发 plugin 命令）
        if text.startswith("/"):
            cmd_name = text[1:].split(maxsplit=1)[0]
            handler = self._registry.get_handler(cmd_name)
            if handler is not None:
                cmd_msg = ws_messages.build_message(
                    channel=channel, source=nick, content=text,
                )
                try:
                    await handler.on_command(cmd_name, cmd_msg)
                except Exception:
                    log.exception("[router] plugin %s on_command error (from IRC)", handler.name)
                await self._registry.broadcast_message(cmd_msg)
                return

        # 普通消息：对外广播（content 保留原 IRC 编码，bridge 自己 parse）
        ws_msg = ws_messages.build_message(
            channel=channel,
            source=nick,
            content=body,
            message_id=parsed.get("message_id"),
        )
        await self._ws.broadcast(ws_msg)
        await self._registry.broadcast_message(ws_msg)

    async def emit_event(self, channel: str, event: str, data: dict | None = None) -> None:
        """core/plugin 发 event 的统一出口。

        三路广播：
          1. WS → 所有 bridge 收到 event
          2. plugin broadcast → 其他 plugin 订阅
          3. IRC __zchat_sys: → channel 内的 agent 感知（如 mode_changed）
        """
        msg = ws_messages.build_event(channel, event, data or {})
        await self._ws.broadcast(msg)
        await self._registry.broadcast_event(msg)

        # IRC sys 消息通知 channel 内的 agent。
        # IRC PRIVMSG 512 字节硬上限：agent 不需要完整 data.text（原消息它已收到），
        # 只需要 event + channel + 关键 meta。所以给 IRC 路径发**瘦身版 payload**：
        # 截断 text/content/message 字段到 ~200 bytes。WS 路径已发 full data。
        if channel:
            try:
                irc_data = _slim_for_irc(data or {})
                payload = irc_encoding.make_sys_payload(
                    nick="cs-bot",
                    sys_type=event,
                    body=irc_data,
                )
                irc_channel = channel if channel.startswith("#") else f"#{channel}"
                self._irc.privmsg(irc_channel, irc_encoding.encode_sys(payload))
            except Exception:
                log.exception("[router] irc sys broadcast failed for event=%s channel=%s",
                              event, channel)
