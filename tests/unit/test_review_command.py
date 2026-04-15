"""Unit 测试 /review 命令解析 + SLA breach 告警格式。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from protocol.commands import parse_command
from protocol.event import Event, EventType


# ------------------------------------------------------------------ #
# /review 命令解析
# ------------------------------------------------------------------ #


def test_review_command_parse() -> None:
    """/review 被 CommandParser 正确解析。"""
    cmd = parse_command("/review")
    assert cmd is not None
    assert cmd.name == "review"
    assert cmd.args == {}


# ------------------------------------------------------------------ #
# /review handler — EventBus 聚合统计
# ------------------------------------------------------------------ #


def test_review_returns_stats() -> None:
    """有对话和事件时 /review 返回格式化统计文本。"""
    from server import wire_bridge_callbacks, build_components

    with patch("server.CS_DB_PATH", ":memory:"), \
         patch("server.CS_EVENT_DB_PATH", ":memory:"), \
         patch("server.CS_MESSAGE_DB_PATH", ":memory:"), \
         patch("server.CS_ROUTING_CONFIG", "/nonexistent/routing.toml"):
        components = build_components()

    conv_manager = components["conversation_manager"]
    event_bus = components["event_bus"]
    bridge = MagicMock()
    bridge.send_reply = AsyncMock()
    bridge.send_event = AsyncMock()
    bridge.on_operator_join = None
    bridge.on_operator_command = None
    bridge.on_admin_command = None
    bridge.on_customer_message = None
    bridge.on_customer_connect = None

    wire_bridge_callbacks(bridge, components)

    # 创建一个对话，走完生命周期到 resolved + CSAT
    conv_manager.create(conversation_id="conv-001", metadata={})
    conv_manager.activate("conv-001")
    conv_manager.resolve("conv-001", outcome="resolved", resolved_by="boss")
    conv_manager.set_csat("conv-001", 4)

    now = datetime.now(timezone.utc)
    asyncio.run(event_bus.publish(Event(
        type=EventType.MODE_CHANGED,
        conversation_id="conv-001",
        data={"from": "copilot", "to": "takeover"},
        timestamp=now,
    )))

    # 调用 /review handler
    cmd = parse_command("/review")
    msg = {"admin_id": "boss"}
    asyncio.run(bridge.on_admin_command(msg, cmd))

    bridge.send_reply.assert_called()
    text = ""
    for call in bridge.send_reply.call_args_list:
        t = call.kwargs.get("text", "")
        if "[review]" in t:
            text = t
            break

    assert "[review]" in text
    assert "对话数: 1" in text
    assert "接管次数: 1" in text
    assert "结案率: 100.0%" in text
    assert "CSAT 均分: 4.0" in text

    # 清理
    components["event_bus"].close()
    components["conversation_manager"].close_db()
    components["message_store"].close()


def test_review_empty_data() -> None:
    """无数据时 /review 返回 "暂无统计数据"。"""
    from server import wire_bridge_callbacks, build_components

    with patch("server.CS_DB_PATH", ":memory:"), \
         patch("server.CS_EVENT_DB_PATH", ":memory:"), \
         patch("server.CS_MESSAGE_DB_PATH", ":memory:"), \
         patch("server.CS_ROUTING_CONFIG", "/nonexistent/routing.toml"):
        components = build_components()

    bridge = MagicMock()
    bridge.send_reply = AsyncMock()
    bridge.send_event = AsyncMock()
    bridge.on_operator_join = None
    bridge.on_operator_command = None
    bridge.on_admin_command = None
    bridge.on_customer_message = None
    bridge.on_customer_connect = None

    wire_bridge_callbacks(bridge, components)

    cmd = parse_command("/review")
    msg = {"admin_id": "boss"}
    asyncio.run(bridge.on_admin_command(msg, cmd))

    bridge.send_reply.assert_called()
    call_kwargs = bridge.send_reply.call_args
    text = call_kwargs.kwargs.get("text", "")
    assert "暂无统计数据" in text

    components["event_bus"].close()
    components["conversation_manager"].close_db()
    components["message_store"].close()


# ------------------------------------------------------------------ #
# SLA breach 告警
# ------------------------------------------------------------------ #


def test_sla_breach_alert_format() -> None:
    """SLA breach 告警消息包含 conv_id + breach 类型 + 超时时长。"""
    from server import wire_bridge_callbacks, build_components

    with patch("server.CS_DB_PATH", ":memory:"), \
         patch("server.CS_EVENT_DB_PATH", ":memory:"), \
         patch("server.CS_MESSAGE_DB_PATH", ":memory:"), \
         patch("server.CS_ROUTING_CONFIG", "/nonexistent/routing.toml"):
        components = build_components()

    event_bus = components["event_bus"]
    bridge = MagicMock()
    bridge.send_reply = AsyncMock()
    bridge.send_event = AsyncMock()
    bridge.on_operator_join = None
    bridge.on_operator_command = None
    bridge.on_admin_command = None
    bridge.on_customer_message = None
    bridge.on_customer_connect = None

    wire_bridge_callbacks(bridge, components)

    # 模拟 SLA timer 超时
    asyncio.run(event_bus.publish(Event(
        type=EventType.TIMER_EXPIRED,
        conversation_id="conv-sla-001",
        data={
            "name": "sla_first_reply",
            "action_type": "alert",
            "action_params": {"duration_s": 30},
        },
    )))

    # 验证告警 event
    bridge.send_event.assert_called()
    event_call = None
    for call in bridge.send_event.call_args_list:
        if call.args[0] == "sla.breach" or call.kwargs.get("event_type") == "sla.breach":
            event_call = call
            break
    assert event_call is not None, "expected sla.breach event"
    data = event_call.args[1] if len(event_call.args) > 1 else event_call.kwargs.get("data", {})
    assert data["conversation_id"] == "conv-sla-001"
    assert data["breach_type"] == "sla_first_reply"
    assert data["timeout_seconds"] == 30

    # 验证告警 reply
    reply_calls = [
        c for c in bridge.send_reply.call_args_list
        if "SLA 告警" in (c.kwargs.get("text", "") or "")
    ]
    assert len(reply_calls) >= 1
    alert_text = reply_calls[0].kwargs["text"]
    assert "conv-sla-001" in alert_text
    assert "sla_first_reply" in alert_text
    assert "30" in alert_text

    components["event_bus"].close()
    components["conversation_manager"].close_db()
    components["message_store"].close()


def test_non_sla_timer_no_alert() -> None:
    """非 SLA timer 超时不触发告警。"""
    from server import wire_bridge_callbacks, build_components

    with patch("server.CS_DB_PATH", ":memory:"), \
         patch("server.CS_EVENT_DB_PATH", ":memory:"), \
         patch("server.CS_MESSAGE_DB_PATH", ":memory:"), \
         patch("server.CS_ROUTING_CONFIG", "/nonexistent/routing.toml"):
        components = build_components()

    event_bus = components["event_bus"]
    bridge = MagicMock()
    bridge.send_reply = AsyncMock()
    bridge.send_event = AsyncMock()
    bridge.on_operator_join = None
    bridge.on_operator_command = None
    bridge.on_admin_command = None
    bridge.on_customer_message = None
    bridge.on_customer_connect = None

    wire_bridge_callbacks(bridge, components)

    # 模拟非 SLA timer 超时
    asyncio.run(event_bus.publish(Event(
        type=EventType.TIMER_EXPIRED,
        conversation_id="conv-other",
        data={"name": "idle_timeout", "action_type": "close", "action_params": {}},
    )))

    # 不应有 sla.breach event
    sla_calls = [
        c for c in bridge.send_event.call_args_list
        if c.args[0] == "sla.breach"
    ]
    assert len(sla_calls) == 0

    components["event_bus"].close()
    components["conversation_manager"].close_db()
    components["message_store"].close()
