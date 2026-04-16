"""FeishuBridge 主类 — WSS 长连接 + Bridge API client + 事件编排。

注册 5 个飞书事件：
- im.message.receive_v1 → 消息转发
- im.chat.member.bot.added_v1 → 动态 customer 注册
- im.chat.member.user.added_v1 → 成员权限授予
- im.chat.member.user.deleted_v1 → 成员权限撤销
- im.chat.disbanded_v1 → 群解散归档
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImChatDisbandedV1,
    P2ImChatMemberBotAddedV1,
    P2ImChatMemberUserAddedV1,
    P2ImChatMemberUserDeletedV1,
    P2ImMessageReceiveV1,
)

from feishu_bridge.bridge_api_client import BridgeAPIClient
from feishu_bridge.config import BridgeConfig, load_config
from feishu_bridge.group_manager import GroupManager
from feishu_bridge.message_parsers import parse_message
from feishu_bridge.sender import FeishuSender
from feishu_bridge.visibility_router import VisibilityRouter
from feishu_bridge.ws_client import CardAwareClient

if TYPE_CHECKING:
    pass

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
        self.visibility_router = VisibilityRouter(
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

        # Auto-hijack 回调钩子（由 app 组装时注入）
        # 签名：(conversation_id: str, operator_id: str, text: str) -> None
        self.on_auto_hijack: Callable[[str, str, str], Any] | None = None

        # Bridge API 传输层
        self._bridge_client = BridgeAPIClient(
            config.channel_server_url,
            register_data={
                "type": "register",
                "bridge_type": "feishu",
                "instance_id": "feishu-bridge",
                "capabilities": ["customer", "operator", "admin"],
            },
        )
        self._bridge_client.on_message = self._on_bridge_event

        # 已连接的 conversation（避免重复 connect）
        self._known_conversations: set[str] = set()

        # 消息去重（防止飞书延迟重投导致重复处理）
        self._processed_msg_ids: set[str] = set()

        # Bridge API WebSocket 连接（兼容旧的 _on_card_action 引用）
        self._bridge_ws: Any | None = None

    # ------------------------------------------------------------------
    # 事件处理器
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

        # Squad thread 回复 → 作为 operator side 消息转发到 channel-server
        # lark-oapi message 对象的 thread 字段：parent_id 或 root_id
        is_thread_reply = (
            getattr(msg, "parent_id", None)
            or getattr(msg, "root_id", None)
            or getattr(msg, "thread_id", None)
        )
        if role == "operator":
            import sys as _sys
            print(f"[bridge] operator msg: is_thread={bool(is_thread_reply)} "
                  f"parent={getattr(msg, 'parent_id', None)} "
                  f"root={getattr(msg, 'root_id', None)} "
                  f"thread={getattr(msg, 'thread_id', None)}", file=_sys.stderr)
        if role == "operator" and is_thread_reply:
            conv_id = self.visibility_router.get_conversation_for_squad(chat_id)
            log.info("[thread] operator thread reply detected: chat=%s conv=%s connected=%s",
                     chat_id, conv_id, self._bridge_client.connected)
            if conv_id and self._bridge_client.connected:
                # 发 operator_message — channel-server Gate 会根据 mode 判定 visibility
                self._bridge_client.send({
                    "type": "operator_message",
                    "conversation_id": conv_id,
                    "operator_id": sender_open_id,
                    "text": text,
                })
            return

        # Auto-hijack 检测：已知 operator 在 customer 群内发言 → 触发回调
        if (
            role == "customer"
            and sender_open_id
            and self.group_manager.is_operator_in_customer_chat(sender_open_id, chat_id)
        ):
            self._trigger_auto_hijack(chat_id, sender_open_id, text)

        # ── 转发到 channel-server Bridge API ──────────────────────
        message_id = msg.message_id or ""
        self._forward_to_bridge(role, chat_id, text, message_id, sender_open_id)

    def _trigger_auto_hijack(
        self, conversation_id: str, operator_id: str, text: str
    ) -> None:
        """已注入回调时触发 auto-hijack。"""
        if not self.on_auto_hijack:
            log.debug(
                "auto-hijack triggered but no callback registered: conv=%s op=%s",
                conversation_id,
                operator_id,
            )
            return
        try:
            self.on_auto_hijack(conversation_id, operator_id, text)
        except Exception:
            log.exception(
                "on_auto_hijack callback raised: conv=%s op=%s",
                conversation_id,
                operator_id,
            )

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
    # Bridge API 协议适配（入站：飞书 → channel-server）
    # ------------------------------------------------------------------

    def _forward_customer(
        self, chat_id: str, text: str, message_id: str, sender_id: str,
    ) -> None:
        """Customer 消息转发：首次消息 → connect + squad card + message。"""
        if chat_id not in self._known_conversations:
            self._bridge_client.send({
                "type": "connect",
                "conversation_id": chat_id,
                "sender_id": sender_id,
                "metadata": {"source": "feishu", "name": sender_id},
            })
            # 在 squad 群创建 conversation card（thread root）
            self.visibility_router.on_conversation_created(
                chat_id, metadata={"customer": {"id": sender_id, "name": sender_id}, "source": "feishu"},
            )
            self._known_conversations.add(chat_id)
        self._bridge_client.send({
            "type": "message",
            "conversation_id": chat_id,
            "sender_id": sender_id,
            "text": text,
            "message_id": message_id,
        })

    def _forward_operator(
        self, chat_id: str, text: str, message_id: str, sender_id: str,
    ) -> None:
        """Operator 消息转发：/ 开头 → command，否则 → message。"""
        conv_id = self.visibility_router.get_conversation_for_squad(chat_id)
        if not conv_id:
            log.debug("operator message in squad %s but no active conversation", chat_id)
            return
        if text.startswith("/"):
            self._bridge_client.send({
                "type": "command",
                "conversation_id": conv_id,
                "sender_id": sender_id,
                "command": text,
            })
        else:
            self._bridge_client.send({
                "type": "message",
                "conversation_id": conv_id,
                "sender_id": sender_id,
                "text": text,
            })

    def _forward_admin(
        self, chat_id: str, text: str, message_id: str, sender_id: str,
    ) -> None:
        """Admin 命令转发。"""
        self._bridge_client.send({
            "type": "command",
            "sender_id": sender_id,
            "command": text,
        })

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
    # Bridge API 协议适配（出站：channel-server → 飞书）
    # ------------------------------------------------------------------

    def _handle_reply_event(self, conv_id: str, msg: dict) -> None:
        """处理 reply / message 事件 → VisibilityRouter 按 visibility 路由。"""
        self.visibility_router.route(conv_id, msg)

    def _handle_edit_event(self, conv_id: str, msg: dict) -> None:
        """处理 edit 事件。"""
        cs_msg_id = msg.get("message_id", "")
        text = msg.get("text", "")
        self.visibility_router.on_edit(conv_id, cs_msg_id, text)

    def _handle_conv_created(self, conv_id: str, msg: dict) -> None:
        """处理 conversation.created 事件。"""
        metadata = msg.get("metadata", {})
        self.visibility_router.on_conversation_created(conv_id, metadata)

    def _handle_mode_changed(self, conv_id: str, msg: dict) -> None:
        """处理 mode.changed 事件。"""
        mode = msg.get("mode", "fast")
        self.visibility_router.on_mode_changed(conv_id, mode)

    def _handle_conv_closed(self, conv_id: str, msg: dict) -> None:
        """处理 conversation.closed 事件。"""
        resolution = msg.get("resolution")
        self.visibility_router.on_conversation_closed(conv_id, resolution)

    def _handle_csat_request(self, conv_id: str, msg: dict) -> None:
        """处理 csat_request 事件。"""
        self.visibility_router.route(conv_id, msg)

    _EVENT_HANDLERS: dict[str, str] = {
        "reply": "_handle_reply_event",      # v1 backward compat
        "message": "_handle_reply_event",    # v2: same handler
        "edit": "_handle_edit_event",
        "conversation.created": "_handle_conv_created",
        "mode.changed": "_handle_mode_changed",
        "conversation.closed": "_handle_conv_closed",
        "csat_request": "_handle_csat_request",
    }

    def _on_bridge_event(self, msg: dict) -> None:
        """处理从 channel-server 收到的 Bridge API 事件。"""
        msg_type = msg.get("type", "")
        conv_id = msg.get("conversation_id", "")

        handler_name = self._EVENT_HANDLERS.get(msg_type)
        if handler_name is not None:
            handler = getattr(self, handler_name)
            handler(conv_id, msg)
        elif msg_type in ("registered", "customer_connected"):
            log.debug("ack: %s", msg_type)
        else:
            log.debug("unhandled bridge event: %s", msg_type)

    # ------------------------------------------------------------------
    # 卡片回调 (card.action.trigger)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_card_action(payload: dict) -> tuple[int, str] | tuple[None, None]:
        """从卡片回调 payload 解析 score 和 conv_id。

        Returns:
            (score, conv_id) 或 (None, None) 如果字段缺失。
        """
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
        """卡片点击回调 → 分发：hijack/resolve 转为命令，CSAT 转为评分。"""
        try:
            value = payload["action"]["value"]
        except (KeyError, TypeError):
            log.debug("card action ignored: invalid payload %s", payload)
            return

        action_type = value.get("action")  # "hijack" or "resolve"
        conv_id = value.get("conv_id")

        if action_type and conv_id:
            # hijack/resolve → 转发为 command (v2)
            self._bridge_client.send({
                "type": "command",
                "conversation_id": conv_id,
                "sender_id": "card_action",
                "command": f"/{action_type}",
            })
            return

        # CSAT score handling
        score = value.get("score")
        if score is not None and conv_id is not None:
            if not self._bridge_client.connected:
                log.warning("card action: bridge not connected")
                return
            self._bridge_client.send({
                "type": "customer_message",
                "conversation_id": conv_id,
                "csat_score": int(score),
            })

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
            # data 可能是 P2CardActionTrigger 对象或 dict
            if hasattr(data, "event"):
                event = data.event
                # event 可能是对象或 dict
                if hasattr(event, "action"):
                    action_obj = event.action
                    # action 可能是对象，提取 value 和 tag
                    if hasattr(action_obj, "value"):
                        value = action_obj.value
                        tag = getattr(action_obj, "tag", "")
                        # value 可能是 dict 或对象
                        if not isinstance(value, dict):
                            value = vars(value) if hasattr(value, "__dict__") else {}
                        self._on_card_action({"action": {"value": value, "tag": tag}})
                        return
                elif isinstance(event, dict):
                    action = event.get("action", {})
                    if action:
                        self._on_card_action({"action": action})
                        return
            # fallback: 尝试直接当 dict
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
        # 1. 先连接 channel-server Bridge API（后台线程）
        self._bridge_client.start()
        import time
        time.sleep(1)  # 等 WebSocket 连接建立
        log.info("Bridge API client started → %s", self.config.channel_server_url)

        # 2. 再启动飞书 WSS 长连接（阻塞）
        handler = self.build_event_handler()
        cli = CardAwareClient(
            self.config.feishu.app_id,
            self.config.feishu.app_secret,
            event_handler=handler,
            card_handler=self._on_card_action,
            log_level=lark.LogLevel.DEBUG,
        )
        cli.start()
