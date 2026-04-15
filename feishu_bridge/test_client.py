"""E2E 测试辅助工具 — 飞书 API 封装。

用于 Phase Final 的端到端测试：发消息、拉消息、断言消息出现/缺席。
"""

from __future__ import annotations

import json
import logging
import time

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageRequest,
    ListMessageRequest,
)

log = logging.getLogger("feishu-bridge.test_client")


class FeishuTestClient:
    """飞书 API 封装，用于 E2E 自动化测试。"""

    def __init__(self, app_id: str, app_secret: str) -> None:
        self.client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )

    def send_message(self, chat_id: str, text: str) -> str:
        """发文本消息，返回 message_id。"""
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
        resp = self.client.im.v1.message.create(req)
        if not resp.success():
            raise RuntimeError(f"send_message failed: {resp.code} {resp.msg}")
        return resp.data.message_id

    def send_card(self, chat_id: str, card: dict) -> str:
        """发卡片消息，返回 message_id。"""
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
        resp = self.client.im.v1.message.create(req)
        if not resp.success():
            raise RuntimeError(f"send_card failed: {resp.code} {resp.msg}")
        return resp.data.message_id

    def list_messages(
        self, chat_id: str, start_time: str, page_size: int = 50
    ) -> list[dict]:
        """拉取群内指定时间后的消息。"""
        req = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .start_time(start_time)
            .page_size(page_size)
            .build()
        )
        resp = self.client.im.v1.message.list(req)
        if not resp.success():
            log.warning("list_messages failed: %s %s", resp.code, resp.msg)
            return []
        items = resp.data.items or []
        results = []
        for item in items:
            results.append(
                {
                    "message_id": item.message_id,
                    "msg_type": item.msg_type,
                    "content": item.body.content if item.body else "",
                    "create_time": item.create_time,
                }
            )
        return results

    def get_message(self, message_id: str) -> dict:
        """获取单条消息详情。"""
        req = GetMessageRequest.builder().message_id(message_id).build()
        resp = self.client.im.v1.message.get(req)
        if not resp.success():
            raise RuntimeError(f"get_message failed: {resp.code} {resp.msg}")
        item = resp.data.items[0] if resp.data.items else None
        if not item:
            return {}
        return {
            "message_id": item.message_id,
            "msg_type": item.msg_type,
            "content": item.body.content if item.body else "",
            "update_time": item.update_time,
        }

    def assert_message_appears(
        self, chat_id: str, contains: str, timeout: int = 30
    ) -> dict:
        """轮询直到群内出现包含指定文本的消息。"""
        start = time.time()
        start_ts = str(int(start))
        while time.time() - start < timeout:
            messages = self.list_messages(chat_id, start_time=start_ts)
            for m in messages:
                if contains in m.get("content", ""):
                    return m
            time.sleep(2)
        raise AssertionError(
            f"Message containing '{contains}' not found in {chat_id} within {timeout}s"
        )

    def assert_message_absent(
        self, chat_id: str, contains: str, wait: int = 10
    ) -> None:
        """等待一段时间，确认群内没有包含指定文本的消息。"""
        start_ts = str(int(time.time()))
        time.sleep(wait)
        messages = self.list_messages(chat_id, start_time=start_ts)
        for m in messages:
            if contains in m.get("content", ""):
                raise AssertionError(
                    f"Message containing '{contains}' should NOT appear in {chat_id}"
                )
