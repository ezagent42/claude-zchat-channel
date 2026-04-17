"""E2E: infra 命令 /abandon → WebSocket 端到端验证。"""

from __future__ import annotations

import asyncio
import json
import os

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_abandon_e2e_flow(bridge_ws, channel_server):
    """TC-E01: customer_connect → /abandon → 收到 conversation.closed event + system reply。"""
    conv_id = f"e2e_abandon_{os.getpid()}_01"

    # 1. 创建对话
    await bridge_ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "alice", "name": "Alice"},
            }
        )
    )
    ack = json.loads(await asyncio.wait_for(bridge_ws.recv(), timeout=5))
    assert ack["type"] == "customer_connected"

    # 2. /abandon
    await bridge_ws.send(
        json.dumps(
            {
                "type": "operator_command",
                "conversation_id": conv_id,
                "operator_id": "op-e2e",
                "command": "/abandon",
            }
        )
    )

    # 3. 收到 conversation.closed event + system reply
    msgs = []
    for _ in range(4):
        try:
            raw = await asyncio.wait_for(bridge_ws.recv(), timeout=5)
            msgs.append(json.loads(raw))
        except asyncio.TimeoutError:
            break

    event_types = [m.get("event_type") for m in msgs if m.get("type") == "event"]
    assert "conversation.closed" in event_types, (
        f"expected conversation.closed event, got: {msgs}"
    )

    closed_events = [
        m for m in msgs
        if m.get("type") == "event" and m.get("event_type") == "conversation.closed"
    ]
    assert closed_events[0]["data"].get("abandoned_by") == "op-e2e"

    # 不应有 CSAT 邀请 reply（和 /resolve 区分）
    replies = [m for m in msgs if m.get("type") in ("reply", "message")]
    assert not any("评分" in r.get("text", "") for r in replies), (
        f"abandon should not send CSAT prompt: {replies}"
    )


