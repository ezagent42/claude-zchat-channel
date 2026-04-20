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
                capabilities=["customer", "operator", "admin"],
            ),
        )
        self._bridge_client.on_message = self._on_bridge_event

        # 已连接的 conversation（避免重复 connect）
        self._known_conversations: set[str] = set()

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
            # 首次入站：connect 事件 + 可选卡片
            if chat_id and chat_id not in self._known_conversations:
                self._bridge_client.send(
                    ws_messages.build_event(
                        channel=target_channel,
                        event="connect",
                        data={
                            "sender_id": sender_id,
                            "metadata": {
                                "source": "feishu",
                                "name": sender_id,
                                "external_chat_id": chat_id,
                            },
                        },
                    )
                )
                self.outbound.on_conversation_created(
                    target_channel,
                    metadata={"source": "feishu", "sender_id": sender_id},
                )
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

    def _handle_edit_event(self, conv_id: str, msg: dict) -> None:
        """处理显式 edit 事件（兼容 channel-server 发 type=edit 的场景）。"""
        cs_msg_id = msg.get("message_id", "")
        content = msg.get("content", "")
        if content:
            parsed = irc_encoding.parse(content)
            text = parsed.get("text", content)
        else:
            text = msg.get("text", "")
        self.outbound.on_edit(conv_id, cs_msg_id, text)

    def _handle_conv_created(self, conv_id: str, msg: dict) -> None:
        """处理 conversation.created 事件。"""
        metadata = msg.get("metadata", {})
        self.outbound.on_conversation_created(conv_id, metadata)

    def _handle_mode_changed(self, conv_id: str, msg: dict) -> None:
        """处理 mode.changed 事件。"""
        mode = msg.get("mode", "fast")
        self.outbound.on_mode_changed(conv_id, mode)

    def _handle_conv_closed(self, conv_id: str, msg: dict) -> None:
        """处理 conversation.closed 事件。"""
        resolution = msg.get("resolution")
        self.outbound.on_conversation_closed(conv_id, resolution)

    def _handle_csat_request(self, conv_id: str, msg: dict) -> None:
        """处理 csat_request 事件。"""
        self.outbound.on_csat_request(conv_id)

    _EVENT_HANDLERS: dict[str, str] = {
        "message": "_handle_message_event",
        "reply": "_handle_message_event",       # 向后兼容
        "edit": "_handle_edit_event",
        "conversation.created": "_handle_conv_created",
        "mode.changed": "_handle_mode_changed",
        "conversation.closed": "_handle_conv_closed",
        "csat_request": "_handle_csat_request",
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
                # 他人 channel：本 bridge 作为 squad 监管，走镜像路径
                if msg_type == "message":
                    self._handle_supervised_message(conv_norm, msg)
                else:
                    log.debug("[recv] supervised non-message event ignored: %s", msg_type)
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
                card = build_conv_card(
                    conv_id,
                    {"source": "supervision", "bot": self._bot_name},
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

        # CSAT score handling
        score = value.get("score")
        if score is not None and conv_id is not None:
            if not self._bridge_client.connected:
                log.warning("card action: bridge not connected")
                return
            self._bridge_client.send(
                ws_messages.build_message(
                    channel=conv_id,
                    source="customer",
                    content=f"__csat_score:{int(score)}",
                )
            )

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
                        self._on_card_action({"action": {"value": value, "tag": tag}})
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
