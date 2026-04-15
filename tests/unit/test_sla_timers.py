"""Unit 测试 SLA Timer 自动触发 + PluginManager (Task 4.6.7)。"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.db import init_db
from engine.event_bus import EventBus
from engine.timer_manager import TimerManager
from plugins import sla_app
from plugins.manager import PluginManager
from protocol.event import EventType


def _seed_conversations(conn, *conv_ids):
    """预插入 conversation 行以满足 FK 约束。"""
    for cid in conv_ids:
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, state, mode, created_at, updated_at) "
            "VALUES (?, 'active', 'auto', '2026-01-01', '2026-01-01')",
            (cid,),
        )
    conn.commit()


def _build_components_with_mem() -> dict:
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
# TC-001 ~ TC-004: sla_onboard
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_sla_onboard_set_on_conversation_created() -> None:
    """TC-001 (P0): plugin fire 后 TimerManager 含 (conv, sla_onboard) 任务。"""
    conn = init_db(":memory:")
    _seed_conversations(conn, "conv-sla-1")
    event_bus = EventBus(conn)
    tm = TimerManager(event_bus)
    components = {"timer_manager": tm}

    with patch("plugins.sla_app.SLA_ONBOARD_DURATION_S", 10.0):
        sla_app.on_conversation_created("conv-sla-1", components)

    # TimerManager 内部 _tasks dict 含该 key
    assert ("conv-sla-1", "sla_onboard") in tm._tasks
    task = tm._tasks[("conv-sla-1", "sla_onboard")]
    assert not task.done()
    task.cancel()
    conn.close()


@pytest.mark.asyncio
async def test_sla_onboard_breach_publishes_event() -> None:
    """TC-002 (P0): duration=0.1s 超时 → EventBus 收到 TIMER_EXPIRED。"""
    conn = init_db(":memory:")
    _seed_conversations(conn, "conv-sla-2")
    event_bus = EventBus(conn)
    tm = TimerManager(event_bus)
    components = {"timer_manager": tm}

    with patch("plugins.sla_app.SLA_ONBOARD_DURATION_S", 0.1):
        sla_app.on_conversation_created("conv-sla-2", components)

    await asyncio.sleep(0.25)

    events = [
        e for e in event_bus.query()
        if e.type == EventType.TIMER_EXPIRED
        and e.conversation_id == "conv-sla-2"
        and e.data.get("name") == "sla_onboard"
    ]
    assert len(events) == 1
    assert events[0].data.get("action_type") == "alert"
    conn.close()


@pytest.mark.asyncio
async def test_sla_onboard_cancelled_by_agent_public_reply() -> None:
    """TC-003 (P0): agent public reply hook 取消 sla_onboard timer，无 breach。"""
    conn = init_db(":memory:")
    _seed_conversations(conn, "conv-sla-3")
    event_bus = EventBus(conn)
    tm = TimerManager(event_bus)
    components = {"timer_manager": tm}

    with patch("plugins.sla_app.SLA_ONBOARD_DURATION_S", 0.3):
        sla_app.on_conversation_created("conv-sla-3", components)

    # 100ms 后 agent 回复
    await asyncio.sleep(0.1)
    sla_app.on_agent_public_message("conv-sla-3", components)

    await asyncio.sleep(0.35)  # 总 > 原 duration

    breach_events = [
        e for e in event_bus.query()
        if e.type == EventType.TIMER_EXPIRED
        and e.conversation_id == "conv-sla-3"
        and e.data.get("name") == "sla_onboard"
    ]
    assert len(breach_events) == 0, "timer should be cancelled, no breach expected"
    assert ("conv-sla-3", "sla_onboard") not in tm._tasks
    conn.close()


@pytest.mark.asyncio
async def test_sla_onboard_not_cancelled_by_side_visibility() -> None:
    """TC-004 (P1): side visibility hook 不触发 cancel。

    实现约定：plugin hook `on_agent_public_message` 只在 visibility=public 时被 fire；
    side visibility 不应 fire 该 hook，因此 plugin 逻辑本身不需要判断 visibility。
    此测试验证：如果上游不 fire，则 timer 保留。
    """
    conn = init_db(":memory:")
    _seed_conversations(conn, "conv-sla-4")
    event_bus = EventBus(conn)
    tm = TimerManager(event_bus)
    components = {"timer_manager": tm}

    with patch("plugins.sla_app.SLA_ONBOARD_DURATION_S", 10.0):
        sla_app.on_conversation_created("conv-sla-4", components)

    # 不调用 on_agent_public_message（模拟上游决定 visibility=side 不 fire）
    assert ("conv-sla-4", "sla_onboard") in tm._tasks
    task = tm._tasks[("conv-sla-4", "sla_onboard")]
    assert not task.done()
    task.cancel()
    conn.close()


# ------------------------------------------------------------------ #
# TC-005 ~ TC-006: PluginManager 加载
# ------------------------------------------------------------------ #


def test_plugin_manager_empty_plugins_dir() -> None:
    """TC-005 (P1): plugins/ 只含 README/__init__ 时 hooks 为空。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "README.md").write_text("noop\n")
        pm = PluginManager(d)
        assert pm.hook_names() == set()


@pytest.mark.asyncio
async def test_plugin_manager_loads_sla_app() -> None:
    """TC-006 (P1): 加载含 sla_app 风格 hook 的模块后，hook_names 含 4 个函数。"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "sample_plugin.py").write_text(
            "async def on_conversation_created(**kw): pass\n"
            "async def on_agent_public_message(**kw): pass\n"
            "async def on_placeholder_sent(**kw): pass\n"
            "async def on_edit_sent(**kw): pass\n"
            "def _helper(): pass  # 应被忽略\n"
            "def other_function(): pass  # 不以 on_ 开头，忽略\n"
        )
        pm = PluginManager(d)
        names = pm.hook_names()
        assert "on_conversation_created" in names
        assert "on_agent_public_message" in names
        assert "on_placeholder_sent" in names
        assert "on_edit_sent" in names
        assert "other_function" not in names
        assert "_helper" not in names

        # fire() 可调用注册的 hooks（不报错）
        await pm.fire("on_conversation_created", conv_id="x", components={})


# ------------------------------------------------------------------ #
# TC-007: 多对话隔离
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_sla_onboard_independent_per_conversation() -> None:
    """TC-007 (P2): 两个 conv 并行 sla_onboard，各自独立。"""
    conn = init_db(":memory:")
    _seed_conversations(conn, "conv-A", "conv-B")
    event_bus = EventBus(conn)
    tm = TimerManager(event_bus)
    components = {"timer_manager": tm}

    with patch("plugins.sla_app.SLA_ONBOARD_DURATION_S", 0.3):
        sla_app.on_conversation_created("conv-A", components)
        sla_app.on_conversation_created("conv-B", components)

    # 100ms 后只取消 A
    await asyncio.sleep(0.1)
    sla_app.on_agent_public_message("conv-A", components)

    await asyncio.sleep(0.35)  # B 应超时

    events_a = [
        e for e in event_bus.query()
        if e.conversation_id == "conv-A" and e.type == EventType.TIMER_EXPIRED
    ]
    events_b = [
        e for e in event_bus.query()
        if e.conversation_id == "conv-B" and e.type == EventType.TIMER_EXPIRED
    ]
    assert len(events_a) == 0, "conv-A should be cancelled"
    assert len(events_b) == 1, "conv-B should breach"
    conn.close()


# ------------------------------------------------------------------ #
# 集成：server build_components + _on_customer_connect fire hook
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_customer_connect_fires_on_conversation_created_hook() -> None:
    """集成：_on_customer_connect 末尾调用 plugin_manager.fire on_conversation_created。"""
    from server import wire_bridge_callbacks

    components = _build_components_with_mem()
    bridge = _make_bridge()
    wire_bridge_callbacks(bridge, components)

    # 替换 plugin_manager 为 mock 以验证 fire 被调用
    mock_pm = MagicMock()
    mock_pm.fire = AsyncMock()
    components["plugin_manager"] = mock_pm

    await bridge.on_customer_connect({"conversation_id": "conv-int-1"})

    # fire 应被调用，hook=on_conversation_created
    assert mock_pm.fire.call_count >= 1
    call = mock_pm.fire.call_args_list[0]
    assert call.args[0] == "on_conversation_created"
    assert call.kwargs["conv_id"] == "conv-int-1"
    assert call.kwargs["components"] is components

    _close_components(components)
