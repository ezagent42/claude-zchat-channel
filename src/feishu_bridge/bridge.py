"""FeishuBridge 主类 — WSS 长连接 + Bridge API client + 事件编排。

注册 5 个飞书事件：
- im.message.receive_v1 → 消息转发
- im.chat.member.bot.added_v1 → 动态 customer 注册
- im.chat.member.user.added_v1 → 成员权限授予
- im.chat.member.user.deleted_v1 → 成员权限撤销
- im.chat.disbanded_v1 → 群解散归档

V4 协议变化：
- 入站消息用 ws_messages.build_message()，不再发 v1 type
- 出站消息不读 visibility 字段；改用 irc_encoding.parse(content).kind 路由
- 不做命令分拣；所有内容原样发 WS，channel-server 自行查 PluginRegistry
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
from pathlib import Path
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImChatDisbandedV1,
    P2ImChatMemberBotAddedV1,
    P2ImChatMemberUserAddedV1,
    P2ImChatMemberUserDeletedV1,
    P2ImMessageReceiveV1,
)

from zchat_protocol import irc_encoding, ws_messages

from feishu_bridge.bridge_api_client import BridgeAPIClient
from feishu_bridge.config import BridgeConfig
from feishu_bridge.group_manager import ChannelMapper
from feishu_bridge.message_parsers import parse_message
from feishu_bridge.outbound import OutboundRouter
from feishu_bridge.routing_reader import (
    read_bridge_mappings,
    read_supervised_channels,
    reverse_mapping,
)
from feishu_bridge.sender import FeishuSender
from feishu_bridge.ws_client import CardAwareClient

log = logging.getLogger("feishu-bridge")


class FeishuBridge:
    """飞书 Bridge 主编排类。"""

    def __init__(self, config: BridgeConfig, routing_path: str | None = None) -> None:
        self.config = config
        self._client = (
            lark.Client.builder()
            .app_id(config.feishu.app_id)
            .app_secret(config.feishu.app_secret)
            .build()
        )
        self.sender = FeishuSender(
            app_id=config.feishu.app_id,
            app_secret=config.feishu.app_secret,
        )

        # 从 routing.toml 加载 external_chat_id → channel_id 映射（bridge 自己用）
        self._routing_path = routing_path or config.routing_path or os.environ.get(
            "CS_ROUTING_CONFIG", "routing.toml"
        )
        # V6: bot_name 是 routing.toml [bots] 里的逻辑名，bridge 用它过滤 channel
        self._bot_name = config.bot_name or config.feishu.app_id
        self._external_to_channel = read_bridge_mappings(self._routing_path, self._bot_name)
        channel_chat_map = reverse_mapping(self._external_to_channel)

        # V6 监管：本 bridge 监管的他人 channels
        # {external_chat_id: channel_id} — 这些 channel 的消息要镜像为卡片到
        # 本 bridge 的飞书群（单 squad_chat 承载所有监管卡片）。
        self._supervised_external_to_channel = read_supervised_channels(
            self._routing_path, self._bot_name
        )

        self.mapper = ChannelMapper(channel_chat_map=channel_chat_map)
        self.outbound = OutboundRouter(sender=self.sender, mapper=self.mapper)

        # 文件下载目录
        self._upload_dir = Path(config.upload_dir)
        self._upload_dir.mkdir(parents=True, exist_ok=True)

        # Bridge API 传输层
        # V6: instance_id 必须唯一（CS 用它作 _connections key），
        # 3 个 bridge 共用 "feishu-bridge" 会导致 CS 只保留最后一个，
        # 广播消息时其他 bridge 收不到 → 出站消息丢失。
        self._bridge_client = BridgeAPIClient(
            config.channel_server_url,
            register_data=ws_messages.build_register(
                bridge_type="feishu",
                instance_id=f"feishu-{self._bot_name}",
                capabilities=[],  # V6 去 role 化；保留字段兼容 protocol schema
            ),
        )
        self._bridge_client.on_message = self._on_bridge_event

        # 已连接的 conversation（避免重复 connect）
        self._known_conversations: set[str] = set()

        # 已发送 chat_info 事件的 chat_id（lazy 拉一次，避免重复 API 调用）
        self._chat_info_emitted: set[str] = set()

        # 监管 bridge 接收的 channel_id → chat_name（来自 customer bridge 的 chat_info event）
        self._supervised_chat_names: dict[str, str] = {}

        # 消息去重（防止飞书延迟重投导致重复处理）
        # 用 deque + set 组合：deque 保持插入顺序并限制容量，set 提供 O(1) 查找
        self._processed_msg_ids: set[str] = set()
        self._processed_msg_order: "collections.deque[str]" = collections.deque(maxlen=10000)

        # Bridge API WebSocket 连接（兼容旧的 _on_card_action 引用）
        self._bridge_ws: Any | None = None

    # ------------------------------------------------------------------
    # V4: routing.toml → channel_chat_map
    # ------------------------------------------------------------------

    def _reload_mappings(self) -> None:
        """重新从 routing.toml 读映射（在 lazy create 后或定期调）。"""
        new_map = read_bridge_mappings(self._routing_path, self._bot_name)
        self._external_to_channel = new_map
        self.mapper.replace_all(reverse_mapping(new_map))
        # 同步 supervision 集合（admin 新建 customer channel 时需要）
        self._supervised_external_to_channel = read_supervised_channels(
            self._routing_path, self._bot_name
        )

    async def _run_cli(self, *args: str, timeout: float = 30.0) -> tuple[int, str, str]:
        """执行 zchat CLI 命令。返回 (returncode, stdout, stderr)。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "zchat", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except asyncio.TimeoutError:
            return -1, "", f"zchat {' '.join(args)} timed out after {timeout}s"
        except FileNotFoundError:
            return -1, "", "'zchat' executable not found in PATH"
        except Exception as e:
            return -1, "", f"subprocess error: {e}"

    async def _lazy_create_channel_and_agent(self, chat_id: str) -> None:
        """bot_added 事件懒创建 channel + agent。

        生成 channel_id = <prefix><chat_id 后缀>，调 CLI:
          zchat channel create <channel_id> --external-chat <chat_id> --bot <bot_name>
          zchat agent create <name> --type <template> --channel <channel_id>
        """
        lc = self.config.lazy_create
        if not lc.enabled:
            return
        # 生成 channel_id（chat_id 通常是 oc_xxx，取 [3:11] 8 字符作为后缀）
        suffix = chat_id[3:11] if len(chat_id) >= 11 else chat_id
        channel_id = f"{lc.channel_prefix}{suffix}"

        # 如已在映射里则跳过
        if chat_id in self._external_to_channel:
            log.info("[lazy] chat_id=%s already mapped to %s, skip", chat_id, channel_id)
            return

        agent_name = f"{channel_id}-agent"

        log.info("[lazy] creating channel=%s agent=%s for chat_id=%s", channel_id, agent_name, chat_id)
        rc, out, err = await self._run_cli(
            "channel", "create", channel_id,
            "--external-chat", chat_id,
            "--bot", self._bot_name,
        )
        if rc != 0:
            log.error("[lazy] channel create failed: rc=%s out=%s err=%s", rc, out, err)
            return

        rc, out, err = await self._run_cli(
            "agent", "create", agent_name,
            "--type", lc.entry_agent_template,
            "--channel", channel_id,
        )
        if rc != 0:
            log.error("[lazy] agent create failed: rc=%s out=%s err=%s", rc, out, err)
            # channel 已创建，保留

        # 刷新本地映射
        self._reload_mappings()

    async def _remove_channel_by_chat(self, chat_id: str) -> None:
        """chat_disbanded 事件清理对应 channel。"""
        channel_id = self._external_to_channel.get(chat_id)
        if not channel_id:
            return
        log.info("[disband] removing channel=%s for chat_id=%s", channel_id, chat_id)
        rc, out, err = await self._run_cli(
            "channel", "remove", channel_id, "--stop-agents",
        )
        if rc != 0:
            log.error("[disband] channel remove failed: rc=%s out=%s err=%s", rc, out, err)
        self._reload_mappings()

    # ------------------------------------------------------------------
    # 事件处理器（飞书入站）
    # ------------------------------------------------------------------

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        """处理群消息事件。"""
        event = data.event
        if not event or not event.message:
            return

        # 自发回环过滤：本 bot 自己发出的消息不应再回经入站事件转回 CS
        # （飞书有时会把 bot 自己 send 的 card / message 也作为 message_receive 事件投回）
        sender_app_id = (
            event.sender.sender_id.app_id
            if event.sender and event.sender.sender_id
               and getattr(event.sender.sender_id, "app_id", None)
            else ""
        )
        if sender_app_id and sender_app_id == self.config.feishu.app_id:
            log.debug("[%s] ignore self-sent message (app_id=%s)",
                      self._bot_name, sender_app_id)
            return

        msg = event.message

        # 消息去重：飞书可能延迟重投同一事件
        # 用 deque + set 双数据结构维护有界 LRU
        msg_id = msg.message_id or ""
        if msg_id and msg_id in self._processed_msg_ids:
            log.debug("Duplicate message %s, skipping", msg_id)
            return
        if msg_id:
            # 如果 deque 已满，最老的 id 会被自动丢出，同步从 set 删除
            if len(self._processed_msg_order) >= self._processed_msg_order.maxlen:
                oldest = self._processed_msg_order[0]
                self._processed_msg_ids.discard(oldest)
            self._processed_msg_order.append(msg_id)
            self._processed_msg_ids.add(msg_id)

        chat_id = msg.chat_id or ""
        # V6: bridge 不分 role；消息都按 chat_id → channel_id 映射转发
        channel_id = self._external_to_channel.get(chat_id)
        if channel_id is None:
            log.debug("Ignoring message from unmapped chat_id=%s", chat_id)
            return
        channel_id = channel_id.lstrip("#")

        # 首次见此 chat_id → 拉群名 + emit chat_info 事件（监管 bridge 用来给卡片标题命名）
        if chat_id and chat_id not in self._chat_info_emitted:
            self._chat_info_emitted.add(chat_id)
            self._emit_chat_info(channel_id, chat_id)

        msg_type = msg.message_type or "text"
        try:
            content = json.loads(msg.content) if msg.content else {}
        except Exception:
            content = {}

        text, file_path = parse_message(msg_type, content, msg, self)

        sender_open_id = (
            event.sender.sender_id.open_id
            if event.sender and event.sender.sender_id
            else ""
        )
        message_id = msg.message_id or ""
        log.info("[%s] %s: %s", self._bot_name, sender_open_id or "?", text[:100])

        # Thread reply 路由：operator 在监管卡片的 thread 里回复 → 用 parent_id 反查 conv_id
        # 把消息包装为 __side: 发回对应 conv channel（V6 监管真正生效的入口）
        parent_id = (
            getattr(msg, "parent_id", None)
            or getattr(msg, "root_id", None)
            or getattr(msg, "thread_id", None)
        )
        target_channel = channel_id
        send_text = text
        if parent_id:
            conv_for_thread = self.outbound.get_conversation_for_card(str(parent_id))
            if conv_for_thread:
                target_channel = conv_for_thread.lstrip("#")
                # operator 在 squad thread 里的回复 = __side: 副驾驶建议（spec §5）
                send_text = irc_encoding.encode_side(text)
                log.info("[thread] operator reply routed to conv=%s as __side:",
                         target_channel)

        self._forward(target_channel, send_text, message_id, sender_open_id, chat_id=chat_id)

    def _on_bot_added(self, data: P2ImChatMemberBotAddedV1) -> None:
        """bot 被拉入新群 → 懒创建 channel + agent（如 enabled）+ 注册 customer。"""
        event = data.event
        if not event:
            return
        chat_id = event.chat_id or ""
        if not chat_id:
            return

        # 先触发懒创建（async），再做 group_manager 本地记录
        if self.config.lazy_create.enabled:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._lazy_create_channel_and_agent(chat_id))
                else:
                    asyncio.run(self._lazy_create_channel_and_agent(chat_id))
            except RuntimeError:
                # 没有 event loop 时直接跑
                asyncio.run(self._lazy_create_channel_and_agent(chat_id))

        log.info("Bot added to group %s (bot=%s)", chat_id, self._bot_name)

    def _emit_chat_info(self, channel_id: str, chat_id: str) -> None:
        """拉飞书群信息 → 经 channel-server 广播 chat_info event。

        监管 bridge 订阅此事件后会缓存 channel_id → chat_name，渲染卡片 title 用。
        失败静默（不阻塞消息转发链）。
        """
        try:
            info = self.sender.get_chat_info(chat_id)
            if not info:
                return
            chat_name = info.get("name") or ""
            if not chat_name:
                return
            self._bridge_client.send(
                ws_messages.build_event(
                    channel=channel_id,
                    event="chat_info",
                    data={"chat_name": chat_name, "chat_id": chat_id},
                )
            )
            log.info("[%s] chat_info emitted: channel=%s chat_name=%s",
                     self._bot_name, channel_id, chat_name)
        except Exception:
            log.exception("[%s] _emit_chat_info failed for chat_id=%s",
                          self._bot_name, chat_id)

    def _on_user_added(self, data: P2ImChatMemberUserAddedV1) -> None:
        """用户加入群 — V6 bridge 不再追踪成员权限（spec §2.2 红线 3）。"""
        # 保留 handler 占位以防飞书 SDK 要求注册；行为留给未来业务 plugin。
        return

    def _on_user_deleted(self, data: P2ImChatMemberUserDeletedV1) -> None:
        """用户退出群 — 同上，V6 不追踪。"""
        return

    def _on_disbanded(self, data: P2ImChatDisbandedV1) -> None:
        """群解散 → 调 CLI 清理 channel + 本地映射清理。"""
        event = data.event
        if not event:
            return
        chat_id = event.chat_id or ""
        if not chat_id:
            return

        # 调 CLI 清理对应 channel（async）
        if chat_id in self._external_to_channel:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._remove_channel_by_chat(chat_id))
                else:
                    asyncio.run(self._remove_channel_by_chat(chat_id))
            except RuntimeError:
                asyncio.run(self._remove_channel_by_chat(chat_id))

        log.info("Group %s disbanded", chat_id)

    # ------------------------------------------------------------------
    # 入站：飞书 → channel-server（V6 统一 _forward，不分 role）
    # ------------------------------------------------------------------

    def _forward(
        self,
        target_channel: str,
        text: str,
        message_id: str,
        sender_id: str,
        *,
        chat_id: str = "",
    ) -> None:
        """统一转发飞书消息到 channel-server Bridge API。

        - 不分 role：role 概念在 V6 已移除，消息对客户/客服/管理员的可见性
          由 IRC 层的 `__msg:` / `__side:` 前缀决定（spec §5）
        - 首次见 chat_id：发 connect 事件 + 可选 squad card（outbound 内部判断）
        - 常规消息：build_message 发 WS
        """
        if not self._bridge_client.connected:
            log.warning("[forward] bridge_client disconnected; dropping chat=%s", chat_id)
            return
        if not target_channel:
            log.warning("[forward] empty target_channel for chat=%s", chat_id)
            return
        try:
            # 首次入站：记录 conv_id（本地去重；无下游订阅者，不再 emit connect event）
            if chat_id and chat_id not in self._known_conversations:
                self._known_conversations.add(chat_id)
            self._bridge_client.send(
                ws_messages.build_message(
                    channel=target_channel,
                    source=sender_id or self._bot_name,
                    content=text,
                    message_id=message_id or None,
                )
            )
        except Exception:
            log.exception("[forward] send failed; message dropped")

    # ------------------------------------------------------------------
    # 出站：channel-server → 飞书（V4 按 irc_encoding.parse kind 路由）
    # ------------------------------------------------------------------

    def _handle_sys_event(self, conv_id: str, msg: dict) -> None:
        """处理 channel-server 系统 event。

        按 conv ownership 分发：
        - own (本 bridge 是 conv 宿主): 响应 csat_request → 发评分卡片到客户群；
          channel_resolved 可做 close 清理（当前 noop）
        - supervisor (本 bridge 监管 conv): 响应 help_requested / help_timeout
          / mode_changed / chat_info / channel_resolved → 更新 squad 卡片
        """
        event_name = msg.get("event") or ""
        data = msg.get("data") or {}
        conv_norm = (conv_id or "").lstrip("#")
        own = {(c or "").lstrip("#") for c in self._external_to_channel.values()}
        supervised = {(c or "").lstrip("#") for c in self._supervised_external_to_channel.values()}
        is_own = conv_norm in own
        is_supervisor = conv_norm in supervised and not is_own
        if not (is_own or is_supervisor):
            return

        # ── own-bridge 专属事件（宿主才处理）─────────────────────────
        if is_own:
            if event_name == "csat_request":
                log.info("[event] csat_request on own %s → send card", conv_norm)
                self.outbound.on_csat_request(conv_norm)
                return
            # 其它 own-only 事件按需加；否则 own 不响应（supervisor 会接）
            if event_name in ("help_requested", "help_timeout", "mode_changed", "chat_info"):
                log.debug("[event] %s on own %s; supervisor handles", event_name, conv_norm)
                return

        # ── supervisor 事件 ─────────────────────────────────────────
        if not is_supervisor:
            return

        if event_name == "help_requested":
            self._supervise_help_requested(conv_norm, data)
        elif event_name == "help_timeout":
            self._supervise_help_timeout(conv_norm, data)
        elif event_name == "mode_changed":
            new_mode = (data or {}).get("to", "fast")
            self.outbound.on_mode_changed(conv_norm, new_mode)
            log.info("[event] mode_changed on supervised %s → %s; card refreshed",
                     conv_norm, new_mode)
        elif event_name in ("channel_resolved", "conversation_resolved"):
            self.outbound.on_conversation_closed(
                conv_norm, (data or {}).get("resolution")
            )
        elif event_name == "chat_info":
            chat_name = (data or {}).get("chat_name") or ""
            if chat_name:
                self._supervised_chat_names[conv_norm] = chat_name
                log.info("[event] cached chat_name for %s = %s", conv_norm, chat_name)
                # 如果 thread/card 已经先建了，回填 metadata + 刷新卡片 title
                thread = self.outbound.get_thread(conv_norm)
                if thread and thread.card_msg_id:
                    thread.metadata["chat_name"] = chat_name
                    try:
                        from feishu_bridge.feishu_renderer import build_conv_card
                        refreshed = build_conv_card(
                            conv_norm,
                            thread.metadata,
                            mode=thread.mode or "fast",
                            state=thread.state or "active",
                        )
                        self.sender.update_card(thread.card_msg_id, refreshed)
                    except Exception:
                        log.exception("[event] refresh card on chat_info failed for %s", conv_norm)
        else:
            log.debug("[event] supervisor ignoring unknown event=%s", event_name)

    def _supervise_help_requested(self, conv_id: str, data: dict) -> None:
        """help_requested 事件 → update_card "🚨 求助中" + reply_in_thread `<at all>`。"""
        thread = self.outbound.get_thread(conv_id)
        if thread is None or not thread.card_msg_id:
            log.warning("[event] help_requested: no card for conv=%s; cannot notify", conv_id)
            return
        text = data.get("text") or ""
        chat_name = self._supervised_chat_names.get(conv_id, "")
        # 1. update card 加紧急标记
        try:
            from feishu_bridge.feishu_renderer import build_conv_card
            card = build_conv_card(
                conv_id,
                {
                    "source": "supervision",
                    "bot": self._bot_name,
                    "alert": "🚨 求助中",
                    "chat_name": chat_name,
                },
                mode="help",
                state="help_requested",
            )
            self.sender.update_card(thread.card_msg_id, card)
        except Exception:
            log.exception("[event] update_card failed for %s", conv_id)
        # 2. thread 内 @所有人
        try:
            mention = '<at user_id="all"></at>'
            body = f'{mention} 🚨 {conv_id} 求助：{text}'
            self.sender.reply_in_thread(thread.card_msg_id, body)
        except Exception:
            log.exception("[event] reply_in_thread @all failed for %s", conv_id)

    def _supervise_help_timeout(self, conv_id: str, data: dict) -> None:
        """help_timeout 事件 → 卡片改 "⚠️ 求助超时" + thread 提醒。"""
        thread = self.outbound.get_thread(conv_id)
        if thread is None or not thread.card_msg_id:
            return
        chat_name = self._supervised_chat_names.get(conv_id, "")
        try:
            from feishu_bridge.feishu_renderer import build_conv_card
            card = build_conv_card(
                conv_id,
                {
                    "source": "supervision",
                    "bot": self._bot_name,
                    "alert": "⚠️ 求助超时",
                    "chat_name": chat_name,
                },
                mode="help",
                state="help_timeout",
            )
            self.sender.update_card(thread.card_msg_id, card)
        except Exception:
            log.exception("[event] update_card timeout failed for %s", conv_id)
        try:
            text = data.get("text") or ""
            self.sender.reply_in_thread(
                thread.card_msg_id,
                f'⚠️ {conv_id} 求助超时（180s 无响应）。原求助：{text}',
            )
        except Exception:
            log.exception("[event] reply_in_thread timeout failed for %s", conv_id)

    def _handle_message_event(self, conv_id: str, msg: dict) -> None:
        """处理 message 事件 → 解析 content 的 kind → OutboundRouter 路由。

        V4 不读 msg["visibility"]。
        irc_encoding.parse(content) 决定种类：
          kind=msg/plain → 客户群 + squad thread
          kind=side      → 仅 squad thread
          kind=edit      → 更新已发消息
          kind=sys       → 忽略（机对机控制不进飞书）
        """
        content = msg.get("content", "")
        parsed = irc_encoding.parse(content)
        kind = parsed.get("kind", "plain")

        if kind == "sys":
            log.debug("[outbound] sys message ignored: conv=%s", conv_id)
            return

        if kind == "edit":
            cs_msg_id = parsed.get("message_id", "")
            text = parsed.get("text", "")
            self.outbound.on_edit(conv_id, cs_msg_id, text)
            return

        # kind ∈ {msg, side, plain}
        text = parsed.get("text", content)
        cs_msg_id = msg.get("message_id") or parsed.get("message_id")
        self.outbound.route(conv_id, kind=kind, text=text, cs_msg_id=cs_msg_id)

    _EVENT_HANDLERS: dict[str, str] = {
        # V6 CS 只向 bridge 发两种 WS type：message 和 event。
        "message": "_handle_message_event",
        "event": "_handle_sys_event",
    }

    def _on_bridge_event(self, msg: dict) -> None:
        """处理从 channel-server 收到的 Bridge API 事件。"""
        msg_type = msg.get("type", "")
        conv_id = msg.get("conversation_id", "") or msg.get("channel", "")
        log.info("[recv] raw type=%s channel=%s", msg_type, conv_id)

        def _norm(ch: str) -> str:
            return (ch or "").lstrip("#")

        if msg_type in self._EVENT_HANDLERS and conv_id:
            own = {_norm(c) for c in self._external_to_channel.values()}
            supervised = {_norm(c) for c in self._supervised_external_to_channel.values()}
            conv_norm = _norm(conv_id)
            if conv_norm in supervised and conv_norm not in own:
                # 他人 channel：本 bridge 作为监管者
                if msg_type == "message":
                    # 镜像路径：原文进 thread + 必要时建卡片
                    self._handle_supervised_message(conv_norm, msg)
                elif msg_type == "event":
                    # 系统事件：监管者按事件类型 update_card / @all 等
                    self._handle_sys_event(conv_norm, msg)
                else:
                    log.debug("[recv] supervised non-message/event ignored: %s", msg_type)
                return
            if conv_norm not in own:
                log.debug("[recv] channel=%s not owned/supervised by bot=%s; skip",
                          conv_id, self._bot_name)
                return

        handler_name = self._EVENT_HANDLERS.get(msg_type)
        if handler_name is not None:
            log.info("[recv] accepted: type=%s channel=%s → %s",
                     msg_type, conv_id, handler_name)
            handler = getattr(self, handler_name)
            handler(conv_id, msg)
        elif msg_type in ("registered", "ack"):
            log.debug("ack: %s", msg_type)
        else:
            log.debug("unhandled bridge event: %s", msg_type)

    # ------------------------------------------------------------------
    # V6 监管：把他人 channel 的消息镜像为卡片 + thread 到本 bot 群
    # ------------------------------------------------------------------

    def _handle_supervised_message(self, conv_id: str, msg: dict) -> None:
        """把受监管 channel 的消息镜像为卡片 + thread 回复到本 bridge 的飞书群。"""
        content = msg.get("content", "")
        source = msg.get("source", "")
        parsed = irc_encoding.parse(content)
        kind = parsed.get("kind", "plain")
        if kind == "sys":
            return
        text = parsed.get("text", content)

        # 本 bridge 的飞书群 chat_id（通常只有一个，如 cs-squad）
        my_chats = list(self._external_to_channel.keys())
        if not my_chats:
            log.debug("[supervise] bot=%s has no own chat; cannot host card",
                      self._bot_name)
            return
        host_chat = my_chats[0]

        thread = self.outbound.get_thread(conv_id)
        if thread is None:
            # 首次见此 conv，发 card 作 thread root
            try:
                from feishu_bridge.feishu_renderer import build_conv_card
                chat_name = self._supervised_chat_names.get(conv_id, "")
                card = build_conv_card(
                    conv_id,
                    {"source": "supervision", "bot": self._bot_name, "chat_name": chat_name},
                    mode="fast",
                    state="active",
                )
                card_msg_id = self.sender.send_card(host_chat, card)
            except Exception:
                log.exception("[supervise] send_card failed for %s", conv_id)
                return
            from feishu_bridge.outbound import ConvThread
            thread = ConvThread(
                conversation_id=conv_id,
                supervising_chat_id=host_chat,
                card_msg_id=card_msg_id,
                state="active",
                # 保存 chat_name 到 metadata：后续 mode_changed/help_requested/timeout
                # 刷新卡片时沿用群名作 title，不必每次重查
                metadata={"chat_name": chat_name} if chat_name else {},
            )
            self.outbound._threads[conv_id] = thread
            log.info("[supervise] card created for %s (msg_id=%s)", conv_id, card_msg_id)

        if not thread.card_msg_id:
            return

        # 按 source / kind 染色 label（仅视觉提示，不影响协议）
        if kind == "side":
            label = "[侧栏]"
        elif source.startswith("ou_") or source == "customer":
            label = "[客户]"
        elif source.startswith(self._bot_name) or "-" in source:
            label = "[AI]"
        else:
            label = f"[{source[:12]}]" if source else ""

        try:
            self.sender.reply_in_thread(thread.card_msg_id, f"{label} {text}".strip())
        except Exception:
            log.exception("[supervise] reply_in_thread failed")

    # ------------------------------------------------------------------
    # 卡片回调 (card.action.trigger)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_card_action(payload: dict) -> tuple[int, str] | tuple[None, None]:
        """从卡片回调 payload 解析 score 和 conv_id。"""
        try:
            value = payload["action"]["value"]
            score_str = value.get("score")
            conv_id = value.get("conv_id")
            if score_str is None or conv_id is None:
                return None, None
            return int(score_str), conv_id
        except (KeyError, TypeError, ValueError):
            return None, None

    def _on_card_action(self, payload: dict) -> None:
        """卡片点击回调 → hijack/resolve 转为命令消息，CSAT 转为评分消息。"""
        try:
            value = payload["action"]["value"]
        except (KeyError, TypeError):
            log.debug("card action ignored: invalid payload %s", payload)
            return

        action_type = value.get("action")  # "hijack" or "resolve"
        conv_id = value.get("conv_id")

        if action_type and conv_id:
            # V4：hijack/resolve → 发 WS message（"/"前缀，channel-server 路由）
            self._bridge_client.send(
                ws_messages.build_message(
                    channel=conv_id,
                    source="card_action",
                    content=f"/{action_type}",
                )
            )
            return

        # CSAT score handling — 走 event 通道，不污染 message/IRC 路径
        score = value.get("score")
        if score is not None and conv_id is not None:
            if not self._bridge_client.connected:
                log.warning("card action: bridge not connected")
                return
            try:
                score_int = int(score)
            except (TypeError, ValueError):
                log.warning("[csat] invalid score value: %s", score)
                return
            self._bridge_client.send(
                ws_messages.build_event(
                    channel=conv_id,
                    event="csat_score",
                    data={"score": score_int, "source": "customer"},
                )
            )
            # UI 反馈：recall 原 CSAT 卡 + 发新"感谢评价"卡
            # （PATCH 对 card shape 大改，飞书部分客户端不刷新 UI；recall+resend 最可靠）
            card_msg_id = self.outbound.pop_csat_card_msg_id(conv_id) or payload.get("card_msg_id") or ""
            customer_chat = self.outbound.mapper.get_feishu_chat(conv_id)
            if customer_chat:
                try:
                    from feishu_bridge.feishu_renderer import thank_you_card
                    # 先发新卡（保持客户视觉连续），再撤回老卡
                    self.sender.send_card(customer_chat, thank_you_card(score_int))
                    if card_msg_id:
                        self.sender.recall(card_msg_id)
                    log.info("[csat] thank-you card sent + old CSAT recalled for %s (score=%d)",
                             conv_id, score_int)
                except Exception:
                    log.exception("[csat] send thank-you / recall failed")
            else:
                log.warning("[csat] no customer_chat mapping for conv=%s", conv_id)

    # ------------------------------------------------------------------
    # 文件下载
    # ------------------------------------------------------------------

    def download_file(self, message_id: str, message) -> str:
        """下载消息附件到本地。"""
        if not message_id:
            return ""
        try:
            from lark_oapi.api.im.v1 import GetMessageResourceRequest

            file_key = ""
            if message and hasattr(message, "content"):
                try:
                    content = json.loads(message.content) if isinstance(message.content, str) else {}
                    file_key = content.get("image_key", "") or content.get("file_key", "")
                except Exception:
                    pass

            if not file_key:
                return ""

            req = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type("image")
                .build()
            )
            resp = self._client.im.v1.message_resource.get(req)
            if not resp.success():
                log.warning("Download failed: %s", resp.msg)
                return ""

            local_path = self._upload_dir / f"{message_id}_{file_key}"
            local_path.write_bytes(resp.file.read())
            return str(local_path)
        except Exception as e:
            log.warning("download_file error: %s", e)
            return ""

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------

    def _on_card_action_event(self, data) -> None:
        """处理通过 EventDispatcherHandler 收到的 card.action.trigger。"""
        try:
            if hasattr(data, "event"):
                event = data.event
                if hasattr(event, "action"):
                    action_obj = event.action
                    if hasattr(action_obj, "value"):
                        value = action_obj.value
                        tag = getattr(action_obj, "tag", "")
                        if not isinstance(value, dict):
                            value = vars(value) if hasattr(value, "__dict__") else {}
                        # 提取 open_message_id（CSAT 点击后 update_card 需要它）
                        # Lark SDK 在不同版本里把它放在 event.context / event.message_id
                        # / data.header 下；防御式尝试几种路径。
                        card_msg_id = ""
                        ctx = getattr(event, "context", None)
                        if ctx is not None:
                            card_msg_id = (
                                getattr(ctx, "open_message_id", "")
                                or getattr(ctx, "message_id", "")
                                or ""
                            )
                            if not card_msg_id and hasattr(ctx, "__dict__"):
                                card_msg_id = ctx.__dict__.get("open_message_id", "") or ""
                        if not card_msg_id:
                            card_msg_id = (
                                getattr(event, "open_message_id", "")
                                or getattr(event, "message_id", "")
                                or ""
                            )
                        log.debug("[card_action] action=%s value=%s card_msg_id=%s",
                                  tag, value, card_msg_id)
                        self._on_card_action({
                            "action": {"value": value, "tag": tag},
                            "card_msg_id": card_msg_id,
                        })
                        return
                elif isinstance(event, dict):
                    action = event.get("action", {})
                    if action:
                        self._on_card_action({"action": action})
                        return
            if isinstance(data, dict):
                self._on_card_action(data)
        except Exception:
            log.exception("_on_card_action_event failed")

    def build_event_handler(self) -> lark.EventDispatcherHandler:
        """构建飞书事件分发器。"""
        builder = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .register_p2_im_chat_member_bot_added_v1(self._on_bot_added)
            .register_p2_im_chat_member_user_added_v1(self._on_user_added)
            .register_p2_im_chat_member_user_deleted_v1(self._on_user_deleted)
            .register_p2_im_chat_disbanded_v1(self._on_disbanded)
        )
        if hasattr(builder, "register_p2_card_action_trigger"):
            builder = builder.register_p2_card_action_trigger(self._on_card_action_event)
        return builder.build()

    def start(self) -> None:
        """启动 Bridge API 客户端 + 飞书 WSS 长连接。"""
        self._bridge_client.start()
        import time
        time.sleep(1)
        log.info("Bridge API client started → %s", self.config.channel_server_url)

        handler = self.build_event_handler()
        cli = CardAwareClient(
            self.config.feishu.app_id,
            self.config.feishu.app_secret,
            event_handler=handler,
            card_handler=self._on_card_action,
            log_level=lark.LogLevel.DEBUG,
        )
        cli.start()
