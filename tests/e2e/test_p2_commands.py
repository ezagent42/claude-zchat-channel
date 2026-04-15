"""E2E: P2 命令 /abandon + /assign + /squad → WebSocket 端到端验证 (Task 4.6.6)。"""

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
    replies = [m for m in msgs if m.get("type") == "reply"]
    assert not any("评分" in r.get("text", "") for r in replies), (
        f"abandon should not send CSAT prompt: {replies}"
    )


async def test_assign_then_squad_e2e(bridge_ws, channel_server):
    """TC-E02: admin /assign → /squad → 收到含分队信息的 system reply。"""
    # 1. /assign agent0 op1
    await bridge_ws.send(
        json.dumps(
            {
                "type": "admin_command",
                "conversation_id": "__admin",
                "admin_id": "boss",
                "command": "/assign agent0 op1",
            }
        )
    )

    # 收集 assign 产生的 event + reply
    for _ in range(3):
        try:
            raw = await asyncio.wait_for(bridge_ws.recv(), timeout=3)
            _ = json.loads(raw)
        except asyncio.TimeoutError:
            break

    # 2. /squad
    await bridge_ws.send(
        json.dumps(
            {
                "type": "admin_command",
                "conversation_id": "__admin",
                "admin_id": "boss",
                "command": "/squad",
            }
        )
    )

    # 3. 收到 system reply（含 op1 + agent0）
    squad_reply = None
    for _ in range(3):
        try:
            raw = await asyncio.wait_for(bridge_ws.recv(), timeout=5)
            m = json.loads(raw)
            if m.get("type") == "reply" and "[squad]" in m.get("text", ""):
                squad_reply = m
                break
        except asyncio.TimeoutError:
            break

    assert squad_reply is not None, "expected [squad] reply"
    assert squad_reply["visibility"] == "system"
    assert "op1" in squad_reply["text"]
    assert "agent0" in squad_reply["text"]
