"""Unit 测试 /resolve /status /dispatch 命令 handler。

使用 build_components() + wire_bridge_callbacks() 创建真实组��（SQLite tmp），
mock BridgeAPIServer 的 send_event/send_reply 验证输出。
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zchat_protocol.commands import Command
from zchat_protocol.participant import Participant, ParticipantRole


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CS_DB_PATH", str(tmp_path / "conv.db"))
    monkeypatch.setenv("BRIDGE_PORT", "0")
    monkeypatch.setenv("AGENT_NAME", "unit-agent")


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """组装组件 + 注入��调，返回 (components, bridge_server)。

    reload server 模块确保 module-level 常量反映当前 env vars。
    """
    import server

    importlib.reload(server)

    components = server.build_components()
    bs = components["bridge_server"]
    bs.send_event = AsyncMock()
    bs.send_reply = AsyncMock()
    server.wire_bridge_callbacks(bs, components)
    yield components, bs

    components["event_bus"].close()
    components["conversation_manager"].close_db()
    components["message_store"].close()


def _create_conv(components, conv_id: str = "conv1") -> None:
    """创建并激活一个测试 conversation。"""
    cm = components["conversation_manager"]
    cm.create(conv_id)
    cm.activate(conv_id)


# ------------------------------------------------------------------ #
# TC-001: /resolve 正常结案
# ------------------------------------------------------------------ #


def test_resolve_calls_resolve_and_emits_event(wired) -> None:
    """TC-001: /resolve → resolve() + conversation.resolved event + CSAT 邀请。"""
    components, bs = wired
    cm = components["conversation_manager"]
    _create_conv(components, "conv_resolve")

    cmd = Command(name="resolve", args={}, raw="/resolve")
    asyncio.run(
        bs.on_operator_command(
            {"conversation_id": "conv_resolve", "operator_id": "xiaoli"},
            cmd,
        )
    )

    # resolve 被调用 → conversation 应该 closed
    conv = cm.get("conv_resolve")
    assert conv is not None
    assert conv.state.value == "closed"

    # event 应发出
    bs.send_event.assert_called()
    event_call = bs.send_event.call_args
    assert event_call[0][0] == "conversation.resolved"
    assert event_call[0][2] == "conv_resolve"

    # CSAT 邀请应发出 (visibility=public)
    bs.send_reply.assert_called()
    reply_call = bs.send_reply.call_args
    assert reply_call[1]["visibility"] == "public" or reply_call[0][2] == "public"


# ------------------------------------------------------------------ #
# TC-002: /resolve conversation 不存在
# ------------------------------------------------------------------ #


def test_resolve_unknown_conv_noop(wired) -> None:
    """TC-002: /resolve 对不存在的 conversation 静默跳过。"""
    components, bs = wired

    cmd = Command(name="resolve", args={}, raw="/resolve")
    asyncio.run(
        bs.on_operator_command(
            {"conversation_id": "nonexist", "operator_id": "xiaoli"},
            cmd,
        )
    )
    bs.send_event.assert_not_called()


# ------------------------------------------------------------------ #
# TC-003: CSAT 评分接收
# ------------------------------------------------------------------ #


def test_csat_score_received(wired) -> None:
    """TC-003: customer_message 带 csat_score → set_csat() 被调用。"""
    components, bs = wired
    cm = components["conversation_manager"]
    _create_conv(components, "conv_csat")

    # 先 resolve（set_csat 要求已有 resolution）
    cm.resolve("conv_csat", outcome="resolved", resolved_by="xiaoli")

    asyncio.run(
        bs.on_customer_message(
            {"conversation_id": "conv_csat", "csat_score": 5}
        )
    )

    # 验证 CSAT 已存储
    conv = cm.get("conv_csat")
    assert conv is not None
    assert conv.resolution is not None
    assert conv.resolution.csat_score == 5


# ------------------------------------------------------------------ #
# TC-004: /status 有活跃对话
# ------------------------------------------------------------------ #


def test_status_returns_active_conversations(wired) -> None:
    """TC-004: /status → 返回格式化的活跃对话列表。"""
    components, bs = wired
    _create_conv(components, "conv_a")
    _create_conv(components, "conv_b")

    cmd = Command(name="status", args={}, raw="/status")
    asyncio.run(
        bs.on_admin_command(
            {"conversation_id": "__admin", "admin_id": "boss"},
            cmd,
        )
    )

    bs.send_reply.assert_called_once()
    call_kwargs = bs.send_reply.call_args[1]
    assert "conv_a" in call_kwargs["text"]
    assert "conv_b" in call_kwargs["text"]
    assert call_kwargs["visibility"] == "system"


# ------------------------------------------------------------------ #
# TC-005: /status 无活跃对话
# ------------------------------------------------------------------ #


def test_status_empty_returns_no_conversations(wired) -> None:
    """TC-005: /status 无活跃对话 → "无活跃对话" 消息。"""
    components, bs = wired

    cmd = Command(name="status", args={}, raw="/status")
    asyncio.run(
        bs.on_admin_command(
            {"conversation_id": "__admin", "admin_id": "boss"},
            cmd,
        )
    )

    bs.send_reply.assert_called_once()
    call_kwargs = bs.send_reply.call_args[1]
    assert "无" in call_kwargs["text"] or "0" in call_kwargs["text"]


# ------------------------------------------------------------------ #
# TC-006: /dispatch 正常分派
# ------------------------------------------------------------------ #


def test_dispatch_adds_agent_participant(wired) -> None:
    """TC-006: /dispatch → add_participant(AGENT) + agent.dispatched event。"""
    components, bs = wired
    _create_conv(components, "conv_dispatch")

    cmd = Command(
        name="dispatch",
        args={"conversation_id": "conv_dispatch", "agent_nick": "deep-agent"},
        raw="/dispatch conv_dispatch deep-agent",
    )
    asyncio.run(
        bs.on_admin_command(
            {"conversation_id": "__admin", "admin_id": "boss"},
            cmd,
        )
    )

    # participant 应被添加
    conv = components["conversation_manager"].get("conv_dispatch")
    agent_ids = [p.id for p in conv.participants if p.role == ParticipantRole.AGENT]
    assert "deep-agent" in agent_ids

    # event 应发出
    bs.send_event.assert_called()
    event_call = bs.send_event.call_args
    assert event_call[0][0] == "agent.dispatched"


# ------------------------------------------------------------------ #
# TC-007: /dispatch conversation 不存在
# ------------------------------------------------------------------ #


def test_dispatch_unknown_conv_noop(wired) -> None:
    """TC-007: /dispatch 对不存在的 conversation 静默跳过。"""
    components, bs = wired

    cmd = Command(
        name="dispatch",
        args={"conversation_id": "nonexist", "agent_nick": "deep-agent"},
        raw="/dispatch nonexist deep-agent",
    )
    asyncio.run(
        bs.on_admin_command(
            {"conversation_id": "__admin", "admin_id": "boss"},
            cmd,
        )
    )
    bs.send_event.assert_not_called()


# ------------------------------------------------------------------ #
# TC-008: admin_command 回调注册
# ------------------------------------------------------------------ #


def test_admin_command_callback_wired(wired) -> None:
    """TC-008: wire_bridge_callbacks 后 on_admin_command 已注册。"""
    _, bs = wired
    assert bs.on_admin_command is not None


# ------------------------------------------------------------------ #
# TC-009: customer_message 回调注册
# ------------------------------------------------------------------ #


def test_customer_message_callback_wired(wired) -> None:
    """TC-009: wire_bridge_callbacks 后 on_customer_message 已注册。"""
    _, bs = wired
    assert bs.on_customer_message is not None


# ------------------------------------------------------------------ #
# TC-010: unknown command 静默跳过
# ------------------------------------------------------------------ #


def test_unknown_operator_command_noop(wired) -> None:
    """TC-010: 未知 operator command 不 crash，不触发 resolve/transition。"""
    components, bs = wired
    _create_conv(components, "conv_unk")

    cmd = Command(name="unknown_thing", args={}, raw="/unknown_thing")
    asyncio.run(
        bs.on_operator_command(
            {"conversation_id": "conv_unk", "operator_id": "xiaoli"},
            cmd,
        )
    )
    bs.send_event.assert_not_called()
