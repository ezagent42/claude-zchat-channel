"""E2E 测试 /review 命令 + SLA breach 告警。

需要 ergo IRC server + channel-server 独立进程。
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_review_command_e2e(bridge_ws, channel_server) -> None:
    """TC-S01: /review → 收到 system visibility reply（统计文本）。"""
    ws = bridge_ws

    # /review（即使无数据也应返回格式化回复）
    await ws.send(
        json.dumps(
            {
                "type": "admin_command",
                "conversation_id": "__admin",
                "admin_id": "boss",
                "command": "/review",
            }
        )
    )

    raw = await asyncio.wait_for(ws.recv(), timeout=5)
    msg = json.loads(raw)
    assert msg["type"] == "reply", f"expected reply, got: {msg}"
    assert msg["visibility"] == "system", f"expected system visibility, got: {msg}"
    assert "[review]" in msg.get("text", ""), f"expected [review] prefix, got: {msg}"


async def test_review_with_conversation(bridge_ws, channel_server) -> None:
    """TC-S02: 创建对话后 /review → 统计包含对话计数。"""
    ws = bridge_ws
    conv_id = f"e2e_review_{os.getpid()}_01"

    # 创建对话
    await ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "test-cust", "name": "Test"},
            }
        )
    )
    # 消费 ack
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ack["type"] == "customer_connected"

    # /review
    await ws.send(
        json.dumps(
            {
                "type": "admin_command",
                "conversation_id": "__admin",
                "admin_id": "boss",
                "command": "/review",
            }
        )
    )

    raw = await asyncio.wait_for(ws.recv(), timeout=5)
    msg = json.loads(raw)
    assert "[review]" in msg.get("text", "")
    assert "对话数:" in msg["text"], f"expected conversation count, got: {msg['text']}"
