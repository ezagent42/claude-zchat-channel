"""飞书消息发送封装 — text / card / edit / reaction。

使用 lark-oapi SDK 的 im.v1.message API。
所有方法提供 *_sync 同步版本（WSS 回调线程中使用）。
"""

from __future__ import annotations

import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageRequest,
    GetChatRequest,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

log = logging.getLogger("feishu-bridge.sender")


class FeishuSender:
    """飞书 API 发送封装。"""

    def __init__(self, app_id: str, app_secret: str) -> None:
        self._client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )

    # ------------------------------------------------------------------
    # send_text
    # ------------------------------------------------------------------

    def send_text_sync(self, chat_id: str, text: str) -> str | None:
        """发送文本消息，返回 message_id 或 None。"""
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.create(req)
        if not resp.success():
            log.warning("send_text failed: %s %s", resp.code, resp.msg)
            return None
        return resp.data.message_id if resp.data else None

    def send_text(self, chat_id: str, text: str) -> str | None:
        """send_text 别名（同步）。"""
        return self.send_text_sync(chat_id, text)

    # ------------------------------------------------------------------
    # send_card
    # ------------------------------------------------------------------

    def send_card_sync(self, chat_id: str, card: dict) -> str | None:
        """发送卡片消息。"""
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card))
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.create(req)
        if not resp.success():
            log.warning("send_card failed: %s %s", resp.code, resp.msg)
            return None
        return resp.data.message_id if resp.data else None

    # ------------------------------------------------------------------
    # update_message (edit)
    # ------------------------------------------------------------------

    def update_message_sync(self, message_id: str, text: str) -> bool:
        """编辑已发送的消息。"""
        body = (
            PatchMessageRequestBody.builder()
            .content(json.dumps({"text": text}))
            .build()
        )
        req = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.patch(req)
        if not resp.success():
            log.warning("update_message failed: %s %s", resp.code, resp.msg)
            return False
        return True

    def update_message(self, message_id: str, text: str) -> bool:
        """update_message 别名（同步）。"""
        return self.update_message_sync(message_id, text)

    # ------------------------------------------------------------------
    # update_card（卡片刷新）
    # ------------------------------------------------------------------

    def update_card_sync(self, message_id: str, card: dict) -> bool:
        """以卡片内容覆盖已发送的 interactive 消息。"""
        body = (
            PatchMessageRequestBody.builder()
            .content(json.dumps(card))
            .build()
        )
        req = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.patch(req)
        if not resp.success():
            log.warning("update_card failed: %s %s", resp.code, resp.msg)
            return False
        return True

    def update_card(self, message_id: str, card: dict) -> bool:
        """update_card 别名（同步）。"""
        return self.update_card_sync(message_id, card)

    # ------------------------------------------------------------------
    # reply_in_thread（thread 回复）
    # ------------------------------------------------------------------

    def reply_in_thread_sync(self, root_msg_id: str, text: str) -> str | None:
        """在指定消息的 thread 中回复文本消息。"""
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .reply_in_thread(True)
            .build()
        )
        req = (
            ReplyMessageRequest.builder()
            .message_id(root_msg_id)
            .request_body(body)
            .build()
        )
        resp = self._client.im.v1.message.reply(req)
        if not resp.success():
            log.warning("reply_in_thread failed: %s %s", resp.code, resp.msg)
            return None
        return resp.data.message_id if resp.data else None

    def reply_in_thread(self, root_msg_id: str, text: str) -> str | None:
        """reply_in_thread 别名（同步）。"""
        return self.reply_in_thread_sync(root_msg_id, text)

    def send_card(self, chat_id: str, card: dict) -> str | None:
        """send_card 别名（同步）。"""
        return self.send_card_sync(chat_id, card)

    # ------------------------------------------------------------------
    # recall (delete) — 撤回已发消息（card / text 都可；限 24h 内本 bot 发的）
    # ------------------------------------------------------------------

    def recall(self, message_id: str) -> bool:
        """撤回消息。飞书 PATCH 对卡片 shape 大改不生效时，用 recall+resend 替代。"""
        try:
            req = DeleteMessageRequest.builder().message_id(message_id).build()
            resp = self._client.im.v1.message.delete(req)
            if not resp.success():
                log.warning("recall failed: %s %s", resp.code, resp.msg)
                return False
            return True
        except Exception:
            log.exception("recall exception for msg_id=%s", message_id)
            return False

    # ------------------------------------------------------------------
    # get_chat_info (im.v1.chat.get) — 取群名等信息
    # ------------------------------------------------------------------

    def get_chat_info(self, chat_id: str) -> dict | None:
        """取飞书群信息。返回 dict {name, description, avatar, ...} 或 None。"""
        try:
            req = GetChatRequest.builder().chat_id(chat_id).build()
            resp = self._client.im.v1.chat.get(req)
            if not resp.success() or not resp.data:
                log.warning("get_chat_info failed: %s %s", resp.code, resp.msg)
                return None
            data = resp.data
            return {
                "name": getattr(data, "name", None) or "",
                "description": getattr(data, "description", None) or "",
                "avatar": getattr(data, "avatar", None) or "",
            }
        except Exception:
            log.exception("get_chat_info exception for chat_id=%s", chat_id)
            return None
