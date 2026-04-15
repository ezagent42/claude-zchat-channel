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

from feishu_bridge.config import BridgeConfig, load_config
from feishu_bridge.group_manager import GroupManager
from feishu_bridge.message_parsers import parse_message
from feishu_bridge.sender import FeishuSender
from feishu_bridge.visibility_router import VisibilityRouter

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

        # 文件下载目录
        self._upload_dir = Path(config.upload_dir)
        self._upload_dir.mkdir(parents=True, exist_ok=True)

        # Auto-hijack 回调钩子（由 app 组装时注入）
        # 签名：(conversation_id: str, operator_id: str, text: str) -> None
        self.on_auto_hijack: Callable[[str, str, str], Any] | None = None

    # ------------------------------------------------------------------
    # 事件处理器
    # ------------------------------------------------------------------

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        """处理群消息事件。"""
        event = data.event
        if not event or not event.message:
            return

        msg = event.message
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

        # Auto-hijack 检测：已知 operator 在 customer 群内发言 → 触发回调
        if (
            role == "customer"
            and sender_open_id
            and self.group_manager.is_operator_in_customer_chat(sender_open_id, chat_id)
        ):
            self._trigger_auto_hijack(chat_id, sender_open_id, text)

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

    def build_event_handler(self) -> lark.EventDispatcherHandler:
        """构建飞书事件分发器。"""
        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .register_p2_im_chat_member_bot_added_v1(self._on_bot_added)
            .register_p2_im_chat_member_user_added_v1(self._on_user_added)
            .register_p2_im_chat_member_user_deleted_v1(self._on_user_deleted)
            .register_p2_im_chat_disbanded_v1(self._on_disbanded)
            .build()
        )

    def start(self) -> None:
        """启动 WSS 长连接。"""
        handler = self.build_event_handler()
        cli = lark.ws.Client(
            self.config.feishu.app_id,
            self.config.feishu.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.DEBUG,
        )
        cli.start()
