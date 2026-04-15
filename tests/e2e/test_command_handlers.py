"""E2E: /resolve /status /dispatch 命令 → WebSocket 端到端验证。

测试完整路径：
  Bridge WS → BridgeAPIServer → on_operator_command / on_admin_command →
  engine 组件 → send_event/send_reply → Bridge WS recv
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_resolve_emits_event_and_csat(bridge_ws, channel_server):
    """TC-E11: customer_connect → /resolve → 收到 conversation.resolved event + CSAT reply。"""
    conv_id = f"e2e_resolve_{os.getpid()}_01"

    # 1. 创建对话
    await bridge_ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "david", "name": "David"},
            }
        )
    )
    await asyncio.sleep(0.5)

    # 2. /resolve
    await bridge_ws.send(
        json.dumps(
            {
                "type": "operator_command",
                "conversation_id": conv_id,
                "operator_id": "xiaoli",
                "command": "/resolve",
            }
        )
    )

    # 3. 收到 conversation.resolved event
    msgs = []
    for _ in range(3):
        try:
            raw = await asyncio.wait_for(bridge_ws.recv(), timeout=5)
            msgs.append(json.loads(raw))
        except asyncio.TimeoutError:
            break

    event_types = [m.get("event_type") for m in msgs if m.get("type") == "event"]
    assert "conversation.resolved" in event_types, (
        f"expected conversation.resolved event, got: {msgs}"
    )

    # CSAT reply 也应收到
    replies = [m for m in msgs if m.get("type") == "reply"]
    assert any("评分" in r.get("text", "") or "csat" in r.get("text", "").lower() for r in replies), (
        f"expected CSAT invitation reply, got replies: {replies}"
    )


async def test_status_returns_formatted_reply(bridge_ws, channel_server):
    """TC-E12: /status → 收到 system visibility reply（格式化状态文本）。

    注意：customer_connect 创建 CREATED 状态的对话，list_active() 只返回 ACTIVE，
    所以 /status 可能返回空列表。验证重点：命令正确执行 + 返回正确格式的 reply。
    """
    # 1. /status（即使无活跃对话，命令应正确返回）
    await bridge_ws.send(
        json.dumps(
            {
                "type": "admin_command",
                "conversation_id": "__admin",
                "admin_id": "boss",
                "command": "/status",
            }
        )
    )

    # 2. 收到 system reply
    raw = await asyncio.wait_for(bridge_ws.recv(), timeout=5)
    msg = json.loads(raw)
    assert msg["type"] == "reply", f"expected reply, got: {msg}"
    assert msg["visibility"] == "system", f"expected system visibility, got: {msg}"
    assert "[status]" in msg.get("text", ""), (
        f"expected [status] prefix in reply text, got: {msg}"
    )


async def test_dispatch_emits_agent_dispatched(bridge_ws, channel_server):
    """TC-E13: customer_connect → /dispatch → 收到 agent.dispatched event。"""
    conv_id = f"e2e_dispatch_{os.getpid()}_01"

    # 1. 创建对话
    await bridge_ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "david", "name": "David"},
            }
        )
    )
    await asyncio.sleep(0.5)

    # 2. /dispatch
    await bridge_ws.send(
        json.dumps(
            {
                "type": "admin_command",
                "conversation_id": "__admin",
                "admin_id": "boss",
                "command": f"/dispatch {conv_id} deep-agent",
            }
        )
    )

    # 3. 收到 agent.dispatched event
    raw = await asyncio.wait_for(bridge_ws.recv(), timeout=5)
    msg = json.loads(raw)
    assert msg["type"] == "event", f"expected event, got: {msg}"
    assert msg["event_type"] == "agent.dispatched", (
        f"expected agent.dispatched, got: {msg}"
    )
    assert msg["data"]["agent_nick"] == "deep-agent"
