"""Unit 测试 P2 命令 handler — /abandon /assign /reassign /squad (spec §8, Task 4.6.6)。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.squad_registry import SquadRegistry
from protocol.commands import parse_command
from protocol.event import EventType


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


# ------------------------------------------------------------------ #
# TC-004 ~ TC-006: /assign & /reassign
# ------------------------------------------------------------------ #


def test_assign_creates_new_squad() -> None:
    """TC-004 (P1): /assign 新增 agent→operator 映射 + squad.assigned event。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    squad_registry = components["squad_registry"]
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    cmd = parse_command("/assign agent0 op1")
    msg = {"admin_id": "admin"}
    asyncio.run(bridge.on_admin_command(msg, cmd))

    assert squad_registry.get_operator("agent0") == "op1"

    events = [
        c for c in bridge.send_event.call_args_list
        if c.args and c.args[0] == "squad.assigned"
    ]
    assert len(events) == 1
    data = events[0].args[1]
    assert data["agent_nick"] == "agent0"
    assert data["operator_id"] == "op1"

    _close_components(components)


def test_assign_overrides_existing() -> None:
    """TC-005 (P1): /assign 再次调用覆盖原 operator。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    squad_registry = components["squad_registry"]
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    # 第一次 assign
    asyncio.run(bridge.on_admin_command(
        {"admin_id": "admin"}, parse_command("/assign agent0 op1")
    ))
    assert squad_registry.get_operator("agent0") == "op1"

    # 第二次 assign 覆盖
    asyncio.run(bridge.on_admin_command(
        {"admin_id": "admin"}, parse_command("/assign agent0 op2")
    ))
    assert squad_registry.get_operator("agent0") == "op2"
    assert "agent0" not in squad_registry.get_squad("op1")
    assert "agent0" in squad_registry.get_squad("op2")

    _close_components(components)


def test_reassign_explicit_migration() -> None:
    """TC-006 (P1): /reassign 显式 from→to 迁移，发 squad.reassigned event。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    squad_registry = components["squad_registry"]
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    squad_registry.assign("agent0", "op1")
    bridge.send_event.reset_mock()

    cmd = parse_command("/reassign agent0 op1 op2")
    msg = {"admin_id": "admin"}
    asyncio.run(bridge.on_admin_command(msg, cmd))

    assert squad_registry.get_operator("agent0") == "op2"

    events = [
        c for c in bridge.send_event.call_args_list
        if c.args and c.args[0] == "squad.reassigned"
    ]
    assert len(events) == 1
    data = events[0].args[1]
    assert data["agent_nick"] == "agent0"
    assert data["from_operator"] == "op1"
    assert data["to_operator"] == "op2"

    _close_components(components)


# ------------------------------------------------------------------ #
# TC-007 ~ TC-009: /squad
# ------------------------------------------------------------------ #


def test_squad_list_all_operators() -> None:
    """TC-007 (P2): /squad 无参数列出全部分队。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    squad_registry = components["squad_registry"]
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    squad_registry.assign("agent0", "op1")
    squad_registry.assign("agent1", "op1")
    squad_registry.assign("agent2", "op2")

    cmd = parse_command("/squad")
    msg = {"admin_id": "admin"}
    asyncio.run(bridge.on_admin_command(msg, cmd))

    reply_texts = [c.kwargs.get("text", "") for c in bridge.send_reply.call_args_list]
    combined = "\n".join(reply_texts)
    assert "op1" in combined
    assert "op2" in combined
    assert "agent0" in combined
    assert "agent1" in combined
    assert "agent2" in combined

    _close_components(components)


def test_squad_list_single_operator() -> None:
    """TC-008 (P2): /squad <op> 只列指定 operator 的 agents。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    squad_registry = components["squad_registry"]
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    squad_registry.assign("agent0", "op1")
    squad_registry.assign("agent1", "op1")
    squad_registry.assign("agent2", "op2")

    cmd = parse_command("/squad op1")
    msg = {"admin_id": "admin"}
    asyncio.run(bridge.on_admin_command(msg, cmd))

    reply_texts = [c.kwargs.get("text", "") for c in bridge.send_reply.call_args_list]
    combined = "\n".join(reply_texts)
    assert "op1" in combined
    assert "agent0" in combined
    assert "agent1" in combined
    # op2 的 agent 不应出现（本次 /squad op1 的结果里）
    op1_reply = next(t for t in reply_texts if "op1" in t)
    assert "agent2" not in op1_reply

    _close_components(components)


def test_squad_empty_returns_message() -> None:
    """TC-009 (P2): SquadRegistry 空时 /squad 返回 "暂无分队"。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    cmd = parse_command("/squad")
    msg = {"admin_id": "admin"}
    asyncio.run(bridge.on_admin_command(msg, cmd))

    reply_texts = [c.kwargs.get("text", "") for c in bridge.send_reply.call_args_list]
    assert any("暂无分队" in t for t in reply_texts), \
        f"expected 暂无分队, got {reply_texts}"

    _close_components(components)


# ------------------------------------------------------------------ #
# TC-010: SquadRegistry.list_all()
# ------------------------------------------------------------------ #


def test_squad_registry_list_all() -> None:
    """TC-010 (P2): SquadRegistry.list_all() 返回 {operator_id: [agent_ids]}。"""
    reg = SquadRegistry()
    reg.assign("agent0", "op1")
    reg.assign("agent1", "op1")
    reg.assign("agent2", "op2")

    snapshot = reg.list_all()
    assert snapshot == {"op1": ["agent0", "agent1"], "op2": ["agent2"]}

    # 快照是拷贝：修改结果不影响内部状态
    snapshot["op1"].append("agent99")
    assert "agent99" not in reg.get_squad("op1")
