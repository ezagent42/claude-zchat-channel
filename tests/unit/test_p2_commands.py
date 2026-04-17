"""Unit 测试 P2 命令 handler — /abandon (spec §8, Task 4.6.6)。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zchat_protocol.commands import parse_command
from zchat_protocol.event import EventType


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _build_components_with_mem() -> dict:
    """用内存 DB 构建 components，避免污染 fs。"""
    from server import build_components

    with patch("server.CS_DB_PATH", ":memory:"), \
         patch("server.CS_ROUTING_CONFIG", "/nonexistent/routing.toml"):
        return build_components()


def _make_bridge() -> MagicMock:
    bridge = MagicMock()
    bridge.send_reply = AsyncMock()
    bridge.send_event = AsyncMock()
    bridge.send_card = AsyncMock()
    bridge.on_operator_join = None
    bridge.on_operator_command = None
    bridge.on_admin_command = None
    bridge.on_customer_message = None
    bridge.on_customer_connect = None
    return bridge


def _close_components(components: dict) -> None:
    components["event_bus"].close()
    components["conversation_manager"].close_db()
    components["message_store"].close()


# ------------------------------------------------------------------ #
# TC-001 ~ TC-003: /abandon
# ------------------------------------------------------------------ #


def test_abandon_closes_active_conversation() -> None:
    """TC-001 (P0): /abandon 关闭活跃对话，conv.state == closed。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    conv_manager = components["conversation_manager"]
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    conv_manager.create(conversation_id="conv-abd-001", metadata={})
    conv_manager.activate("conv-abd-001")

    cmd = parse_command("/abandon")
    msg = {"operator_id": "op-1", "conversation_id": "conv-abd-001"}
    asyncio.run(bridge.on_operator_command(msg, cmd))

    conv = conv_manager.get("conv-abd-001")
    assert conv is not None
    assert conv.state.value == "closed", f"expected closed, got {conv.state.value}"

    _close_components(components)


def test_abandon_does_not_send_csat() -> None:
    """TC-002 (P0): /abandon 不发 CSAT 卡片，不标 outcome。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    conv_manager = components["conversation_manager"]
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    conv_manager.create(conversation_id="conv-abd-002", metadata={})
    conv_manager.activate("conv-abd-002")

    cmd = parse_command("/abandon")
    msg = {"operator_id": "op-1", "conversation_id": "conv-abd-002"}
    asyncio.run(bridge.on_operator_command(msg, cmd))

    # 不发 csat_request 卡片
    for call in bridge.send_card.call_args_list:
        kwargs = call.kwargs
        args = call.args
        card_type = kwargs.get("card_type") or (args[0] if args else None)
        assert card_type != "csat_request", "abandon should not send csat_request card"

    # reply 文本不应是 "请评分"
    for call in bridge.send_reply.call_args_list:
        text = call.kwargs.get("text", "")
        assert "请评分" not in text, f"abandon should not ask for CSAT: {text!r}"

    # 没有标记 outcome
    conv = conv_manager.get("conv-abd-002")
    assert conv.resolution is None or conv.resolution.outcome != "resolved"

    _close_components(components)


def test_abandon_emits_conversation_closed_event() -> None:
    """TC-003 (P0): /abandon 发布 conversation.closed event 给 EventBus。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    conv_manager = components["conversation_manager"]
    event_bus = components["event_bus"]
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    conv_manager.create(conversation_id="conv-abd-003", metadata={})
    conv_manager.activate("conv-abd-003")

    cmd = parse_command("/abandon")
    msg = {"operator_id": "op-xyz", "conversation_id": "conv-abd-003"}
    asyncio.run(bridge.on_operator_command(msg, cmd))

    events = event_bus.query()
    closed = [
        e for e in events
        if e.type == EventType.CONVERSATION_CLOSED
        and e.conversation_id == "conv-abd-003"
    ]
    assert len(closed) >= 1, "expected conversation.closed event"
    assert closed[0].data.get("abandoned_by") == "op-xyz"

    # bridge.send_event 也应该收到 conversation.closed
    closed_bridge = [
        c for c in bridge.send_event.call_args_list
        if c.args and c.args[0] == "conversation.closed"
    ]
    assert len(closed_bridge) >= 1

    _close_components(components)


