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

import json
import logging
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
from feishu_bridge.config import BridgeConfig, load_config
from feishu_bridge.group_manager import GroupManager
from feishu_bridge.message_parsers import parse_message
from feishu_bridge.outbound import OutboundRouter
from feishu_bridge.sender import FeishuSender
from feishu_bridge.ws_client import CardAwareClient

log = logging.getLogger("feishu-bridge")


class FeishuBridge:
    """飞书 Bridge 主编排类。"""

    def __init__(self, config: BridgeConfig) -> None:
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
        self.group_manager = GroupManager(
            admin_chat_id=config.groups.admin_chat_id,
            squad_chats=config.groups.squad_chats,
            customer_chats_path=config.customer_chats_path,
        )
        # V4：出站路由器（按 irc_encoding kind 路由，不读 visibility 字段）
        self.outbound = OutboundRouter(
            sender=self.sender,
            group_manager=self.group_manager,
            admin_chat_id=config.groups.admin_chat_id,
        )

        # 预注册 customer chats（可选，正式环境通过 bot_added 事件动态注册）
        for chat_id in getattr(config.groups, 'customer_chats', []):
            self.group_manager.register_customer_chat(chat_id)

        # 文件下载目录
        self._upload_dir = Path(config.upload_dir)
        self._upload_dir.mkdir(parents=True, exist_ok=True)

        # Bridge API 传输层
        self._bridge_client = BridgeAPIClient(
            config.channel_server_url,
            register_data=ws_messages.build_register(
                bridge_type="feishu",
                instance_id="feishu-bridge",
                capabilities=["customer", "operator", "admin"],
            ),
        )
        self._bridge_client.on_message = self._on_bridge_event

        # 已连接的 conversation（避免重复 connect）
        self._known_conversations: set[str] = set()

        # agent nick 识别模式（用于出站路由的 gate 逻辑）
        self._agent_nick_pattern: str = getattr(
            getattr(config, "agents", None), "nick_pattern", "-agent"
        )

        # 消息去重（防止飞书延迟重投导致重复处理）
        self._processed_msg_ids: set[str] = set()

        # Bridge API WebSocket 连接（兼容旧的 _on_card_action 引用）
        self._bridge_ws: Any | None = None

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
        msg_id = msg.message_id or ""
        if msg_id and msg_id in self._processed_msg_ids:
            log.debug("Duplicate message %s, skipping", msg_id)
            return
        if msg_id:
            self._processed_msg_ids.add(msg_id)

        chat_id = msg.chat_id or ""
        role = self.group_manager.identify_role(chat_id)

        if role == "unknown":
            log.debug("Ignoring message from unknown group %s", chat_id)
            return

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
        log.info("[%s] %s: %s", role, sender_open_id or "?", text[:100])

        # Squad thread 回复 → 作为 operator 消息转发到 channel-server
        is_thread_reply = (
            getattr(msg, "parent_id", None)
            or getattr(msg, "root_id", None)
            or getattr(msg, "thread_id", None)
        )
        if role == "operator" and is_thread_reply:
            conv_id = self.outbound.get_conversation_for_squad(chat_id)
            log.info("[thread] operator thread reply: chat=%s conv=%s connected=%s",
                     chat_id, conv_id, self._bridge_client.connected)
            if conv_id and self._bridge_client.connected:
                # V4：operator thread 回复原样发 WS
                self._bridge_client.send(
                    ws_messages.build_message(
                        channel=conv_id,
                        source=sender_open_id or "operator",
                        content=text,
                    )
                )
            return

        # 转发到 channel-server Bridge API
        message_id = msg.message_id or ""
        self._forward_to_bridge(role, chat_id, text, message_id, sender_open_id)

    def _on_bot_added(self, data: P2ImChatMemberBotAddedV1) -> None:
        """bot 被拉入新群 → 自动注册 customer（跳过已配置群）。"""
        event = data.event
        if not event:
            return
        chat_id = event.chat_id or ""
        if chat_id:
            self.group_manager.register_customer_chat(chat_id)
            log.info("Bot added to group %s, role: %s", chat_id, self.group_manager.identify_role(chat_id))

    def _on_user_added(self, data: P2ImChatMemberUserAddedV1) -> None:
        """用户加入群 → 授予角色权限。"""
        event = data.event
        if not event:
            return
        chat_id = event.chat_id or ""
        users = event.users or []
        for user in users:
            user_id = user.user_id.open_id if user.user_id else ""
            if user_id and chat_id:
                self.group_manager.on_member_added(user_id, chat_id)

    def _on_user_deleted(self, data: P2ImChatMemberUserDeletedV1) -> None:
        """用户退出群 → 撤销角色权限。"""
        event = data.event
        if not event:
            return
        chat_id = event.chat_id or ""
        users = event.users or []
        for user in users:
            user_id = user.user_id.open_id if user.user_id else ""
            if user_id and chat_id:
                self.group_manager.on_member_removed(user_id, chat_id)

    def _on_disbanded(self, data: P2ImChatDisbandedV1) -> None:
        """群解散 → 清理 conversation。"""
        event = data.event
        if not event:
            return
        chat_id = event.chat_id or ""
        if chat_id:
            self.group_manager.on_group_disbanded(chat_id)
            log.info("Group %s disbanded", chat_id)

    # ------------------------------------------------------------------
    # 入站：飞书 → channel-server（V4 ws_messages 格式）
    # ------------------------------------------------------------------

    def _forward_customer(
        self, chat_id: str, text: str, message_id: str, sender_id: str,
    ) -> None:
        """Customer 消息转发：首次消息 → connect 事件 + squad card + message。"""
        if chat_id not in self._known_conversations:
            # connect 事件：通知 channel-server 新 conversation
            self._bridge_client.send(
                ws_messages.build_event(
                    channel=chat_id,
                    event="connect",
                    data={
                        "sender_id": sender_id,
                        "metadata": {"source": "feishu", "name": sender_id},
                    },
                )
            )
            # 在 squad 群创建 conversation card（thread root）
            self.outbound.on_conversation_created(
                chat_id,
                metadata={"customer": {"id": sender_id, "name": sender_id}, "source": "feishu"},
            )
            self._known_conversations.add(chat_id)
        # V4：统一用 build_message
        self._bridge_client.send(
            ws_messages.build_message(
                channel=chat_id,
                source=sender_id or "customer",
                content=text,
                message_id=message_id or None,
            )
        )

    def _forward_operator(
        self, chat_id: str, text: str, message_id: str, sender_id: str,
    ) -> None:
        """Operator 消息转发：原样发 WS（不分拣命令）。

        V4：channel-server PluginRegistry 统一处理 / 前缀。
        """
        conv_id = self.outbound.get_conversation_for_squad(chat_id)
        if not conv_id:
            log.debug("operator message in squad %s but no active conversation", chat_id)
            return
        self._bridge_client.send(
            ws_messages.build_message(
                channel=conv_id,
                source=sender_id or "operator",
                content=text,
                message_id=message_id or None,
            )
        )

    def _forward_admin(
        self, chat_id: str, text: str, message_id: str, sender_id: str,
    ) -> None:
        """Admin 命令转发：原样发 WS message。"""
        self._bridge_client.send(
            ws_messages.build_message(
                channel=chat_id,
                source=sender_id or "admin",
                content=text,
                message_id=message_id or None,
            )
        )

    _ROLE_FORWARDERS: dict[str, str] = {
        "customer": "_forward_customer",
        "operator": "_forward_operator",
        "admin": "_forward_admin",
    }

    def _forward_to_bridge(
        self, role: str, chat_id: str, text: str,
        message_id: str, sender_id: str,
    ) -> None:
        """根据角色将飞书消息转发到 channel-server Bridge API。"""
        if not self._bridge_client.connected:
            return
        handler_name = self._ROLE_FORWARDERS.get(role)
        if handler_name is not None:
            handler = getattr(self, handler_name)
            handler(chat_id, text, message_id, sender_id)

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

        handler_name = self._EVENT_HANDLERS.get(msg_type)
        if handler_name is not None:
            handler = getattr(self, handler_name)
            handler(conv_id, msg)
        elif msg_type in ("registered", "ack"):
            log.debug("ack: %s", msg_type)
        else:
            log.debug("unhandled bridge event: %s", msg_type)

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
