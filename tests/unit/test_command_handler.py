"""Unit 测试 engine/command_handler.py — CommandHandler 独立测试。

直接构造 CommandHandler，mock 依赖组件，验证各命令的业务逻辑。
不经过 wire_bridge_callbacks / BridgeAPIServer，确保 engine 层可独立测试。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.command_handler import CommandHandler
from engine.conversation_manager import ConversationManager
from engine.event_bus import EventBus
from engine.message_store import MessageStore
from engine.mode_manager import ModeManager
from routing_config import RoutingConfig
from zchat_protocol.commands import Command
from zchat_protocol.event import EventType
from zchat_protocol.mode import ConversationMode
from zchat_protocol.participant import Participant, ParticipantRole


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture
def components(tmp_path):
    """使用真实 SQLite（tmp）构建核心组件。"""
    from engine.db import init_db

    conn = init_db(str(tmp_path / "test.db"))
    event_bus = EventBus(conn)
    conv_manager = ConversationManager(conn)
    mode_manager = ModeManager(event_bus)
    message_store = MessageStore(conn)

    yield {
        "conn": conn,
        "event_bus": event_bus,
        "conversation_manager": conv_manager,
        "mode_manager": mode_manager,
        "message_store": message_store,
    }

    event_bus.close()
    conv_manager.close_db()
    message_store.close()


@pytest.fixture
def bridge():
    """Mock BridgeAPIServer。"""
    bs = MagicMock()
    bs.send_event = AsyncMock()
    bs.send_reply = AsyncMock()
    return bs


@pytest.fixture
def handler(components, bridge):
    """构造 CommandHandler 实例。"""
    return CommandHandler(
        conv_manager=components["conversation_manager"],
        mode_manager=components["mode_manager"],
        event_bus=components["event_bus"],
        message_store=components["message_store"],
        bridge_server=bridge,
        routing_config=RoutingConfig(),
    )


def _create_conv(components, conv_id: str = "conv1") -> None:
    """创建并激活一个测试 conversation。"""
    cm = components["conversation_manager"]
    cm.create(conv_id)
    cm.activate(conv_id)


# ------------------------------------------------------------------ #
# TC-001: /hijack → TAKEOVER
# ------------------------------------------------------------------ #


def test_hijack_transitions_to_takeover(components, bridge, handler) -> None:
    """TC-001: /hijack 将 conversation mode 切换到 TAKEOVER。"""
    _create_conv(components, "conv_hj")

    cmd = Command(name="hijack", args={}, raw="/hijack")
    asyncio.run(
        handler.execute_operator_command(cmd, "conv_hj", "op1")
    )

    conv = components["conversation_manager"].get("conv_hj")
    assert conv.mode == ConversationMode.TAKEOVER.value

    # mode.changed event 应发出
    bridge.send_event.assert_called()
    event_call = bridge.send_event.call_args
    assert event_call[0][0] == "mode.changed"
    assert event_call[0][1]["to"] == "takeover"

    # hijack 不发额外文本通知（channel-server 不做可见性决策）
    bridge.send_reply.assert_not_called()


# ------------------------------------------------------------------ #
# TC-002: /release → AUTO
# ------------------------------------------------------------------ #


def test_release_transitions_to_auto(components, bridge, handler) -> None:
    """TC-002: /release 将 conversation mode 切换到 AUTO。"""
    _create_conv(components, "conv_rel")
    # 先切到 copilot，再 release 回 auto
    conv = components["conversation_manager"].get("conv_rel")
    conv.mode = ConversationMode.COPILOT.value

    cmd = Command(name="release", args={}, raw="/release")
    asyncio.run(
        handler.execute_operator_command(cmd, "conv_rel", "op1")
    )

    conv = components["conversation_manager"].get("conv_rel")
    assert conv.mode == ConversationMode.AUTO.value

    bridge.send_event.assert_called()
    event_call = bridge.send_event.call_args
    assert event_call[0][1]["to"] == "auto"


# ------------------------------------------------------------------ #
# TC-003: /resolve → 结案 + CSAT 邀请
# ------------------------------------------------------------------ #


def test_resolve_closes_conversation(components, bridge, handler) -> None:
    """TC-003: /resolve 关闭 conversation 并发出 CSAT 邀请。"""
    _create_conv(components, "conv_res")

    cmd = Command(name="resolve", args={}, raw="/resolve")
    asyncio.run(
        handler.execute_operator_command(cmd, "conv_res", "op1")
    )

    conv = components["conversation_manager"].get("conv_res")
    assert conv.state.value == "closed"
    assert conv.resolution is not None
    assert conv.resolution.outcome == "resolved"

    # conversation.resolved event
    bridge.send_event.assert_called()
    assert bridge.send_event.call_args[0][0] == "conversation.resolved"

    # CSAT 邀请
    bridge.send_reply.assert_called()
    reply_kwargs = bridge.send_reply.call_args[1]
    assert reply_kwargs["visibility"] == "public"
    assert "请评分" in reply_kwargs["text"]


# ------------------------------------------------------------------ #
# TC-004: /abandon → 关闭对话（无 CSAT）
# ------------------------------------------------------------------ #


def test_abandon_closes_without_csat(components, bridge, handler) -> None:
    """TC-004: /abandon 关闭对话，不发 CSAT 邀请，不标 outcome。"""
    _create_conv(components, "conv_abd")

    cmd = Command(name="abandon", args={}, raw="/abandon")
    asyncio.run(
        handler.execute_operator_command(cmd, "conv_abd", "op1")
    )

    conv = components["conversation_manager"].get("conv_abd")
    assert conv.state.value == "closed"
    assert conv.resolution is None  # 不标 outcome

    # conversation.closed event
    bridge.send_event.assert_called()
    assert bridge.send_event.call_args[0][0] == "conversation.closed"
    assert bridge.send_event.call_args[0][1]["trigger"] == "abandon"

    # EventBus 中也有 CONVERSATION_CLOSED 事件
    events = components["event_bus"].query()
    closed_events = [
        e for e in events if e.type == EventType.CONVERSATION_CLOSED
    ]
    assert len(closed_events) >= 1
    assert closed_events[0].data["abandoned_by"] == "op1"

    # reply 文本不含 "请评分"
    for call in bridge.send_reply.call_args_list:
        text = call[1].get("text", "")
        assert "请评分" not in text


# ------------------------------------------------------------------ #
# TC-006: /dispatch → 添加 agent participant
# ------------------------------------------------------------------ #


def test_dispatch_adds_participant(components, bridge, handler) -> None:
    """TC-006: /dispatch 将 agent 添加到 conversation 参与者。"""
    _create_conv(components, "conv_disp")

    cmd = Command(
        name="dispatch",
        args={"conversation_id": "conv_disp", "agent_nick": "deep-agent"},
        raw="/dispatch conv_disp deep-agent",
    )
    asyncio.run(handler.execute_admin_command(cmd, "admin1"))

    conv = components["conversation_manager"].get("conv_disp")
    agent_ids = [p.id for p in conv.participants if p.role == ParticipantRole.AGENT]
    assert "deep-agent" in agent_ids

    bridge.send_event.assert_called()
    assert bridge.send_event.call_args[0][0] == "agent.dispatched"


# ------------------------------------------------------------------ #
# TC-008: 未知命令 → 静默跳过
# ------------------------------------------------------------------ #


def test_unknown_command_ignored(components, bridge, handler) -> None:
    """TC-008: 未知 operator 命令不 crash，不触发任何操作。"""
    _create_conv(components, "conv_unk")

    cmd = Command(name="unknown_thing", args={}, raw="/unknown_thing")
    asyncio.run(
        handler.execute_operator_command(cmd, "conv_unk", "op1")
    )

    bridge.send_event.assert_not_called()
    bridge.send_reply.assert_not_called()


# ------------------------------------------------------------------ #
# TC-009: /copilot → COPILOT mode
# ------------------------------------------------------------------ #


def test_copilot_transitions_to_copilot(components, bridge, handler) -> None:
    """TC-009: /copilot 将 conversation mode 切换到 COPILOT。"""
    _create_conv(components, "conv_cp")

    cmd = Command(name="copilot", args={}, raw="/copilot")
    asyncio.run(
        handler.execute_operator_command(cmd, "conv_cp", "op1")
    )

    conv = components["conversation_manager"].get("conv_cp")
    assert conv.mode == ConversationMode.COPILOT.value

    bridge.send_event.assert_called()
    event_call = bridge.send_event.call_args
    assert event_call[0][1]["to"] == "copilot"


# ------------------------------------------------------------------ #
# TC-010: conversation 不存在 → 静默跳过
# ------------------------------------------------------------------ #


def test_operator_command_nonexistent_conv_noop(bridge, handler) -> None:
    """TC-010: operator 命令对不存在的 conversation 静默跳过。"""
    cmd = Command(name="hijack", args={}, raw="/hijack")
    asyncio.run(
        handler.execute_operator_command(cmd, "nonexistent", "op1")
    )

    bridge.send_event.assert_not_called()
    bridge.send_reply.assert_not_called()
