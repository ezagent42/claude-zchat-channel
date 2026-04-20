"""中枢路由：IRC ↔ WS 双向翻译 + 命令分派。"""

from __future__ import annotations
import logging
import uuid
from typing import Any

from zchat_protocol import irc_encoding, ws_messages

from .plugin import PluginRegistry
from .routing import RoutingTable

log = logging.getLogger(__name__)


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
            # 已有前缀（比如 operator 在 side thread 的消息可能被 bridge 加了 __side:）
            encoded = content

        # Mode 决定是否 @ prefix
        if mode in ("auto", "copilot"):
            entry = self._routing.entry_agent(channel)
            if entry:
                try:
                    self._irc.privmsg(irc_channel, f"@{entry} {encoded}")
                    log.info("[router] → IRC %s: @%s %s", irc_channel, entry, encoded[:60])
                except Exception:
                    log.exception("[router] irc privmsg failed")
            else:
                log.warning(
                    "[router] channel %r has no entry_agent; message not delivered to any agent",
                    channel,
                )
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

        # IRC sys 消息通知 channel 内的 agent
        if channel:
            try:
                payload = irc_encoding.make_sys_payload(
                    nick="cs-bot",
                    sys_type=event,
                    body=data or {},
                )
                irc_channel = channel if channel.startswith("#") else f"#{channel}"
                self._irc.privmsg(irc_channel, irc_encoding.encode_sys(payload))
            except Exception:
                log.exception("[router] irc sys broadcast failed for event=%s channel=%s",
                              event, channel)
