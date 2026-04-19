"""ActivationPlugin 单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from plugins.activation.plugin import ActivationPlugin


@pytest.fixture
def emit_event():
    return AsyncMock()


@pytest.fixture
def act(tmp_path, emit_event):
    return ActivationPlugin(state_file=tmp_path / "activation.json", emit_event=emit_event)


def test_no_commands(act):
    assert act.handles_commands() == []


@pytest.mark.asyncio
async def test_on_message_updates_last_activity(act):
    await act.on_ws_message({"channel": "c1", "source": "customer", "content": "hi"})
    assert act.query("last_activity", {"channel": "c1"}) is not None


@pytest.mark.asyncio
async def test_channel_resolved_marks_dormant(act):
    await act.on_ws_event({"event": "channel_resolved", "channel": "c1", "data": {}})
    assert act.query("is_dormant", {"channel": "c1"}) is True
    assert act.query("last_closed", {"channel": "c1"}) is not None


@pytest.mark.asyncio
async def test_customer_return_emits_event(act, emit_event):
    # 先 resolve
    await act.on_ws_event({"event": "channel_resolved", "channel": "c1", "data": {}})
    # 客户回访
    await act.on_ws_message({"channel": "c1", "source": "customer_xyz", "content": "在吗"})

    emit_event.assert_awaited_once()
    args = emit_event.call_args[0]
    assert args[0] == "customer_returned"
    assert args[1] == "c1"
    assert args[2]["sender"] == "customer_xyz"
    assert "在吗" in args[2]["preview"]

    # 再次发消息不再触发（已 unset dormant）
    emit_event.reset_mock()
    await act.on_ws_message({"channel": "c1", "source": "customer_xyz", "content": "again"})
    emit_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_return_event_if_not_dormant(act, emit_event):
    await act.on_ws_message({"channel": "c1", "source": "customer", "content": "hi"})
    emit_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_persistence(tmp_path, emit_event):
    p = tmp_path / "state.json"
    a = ActivationPlugin(state_file=p, emit_event=emit_event)
    await a.on_ws_event({"event": "channel_resolved", "channel": "c1", "data": {}})

    b = ActivationPlugin(state_file=p, emit_event=emit_event)
    assert b.query("is_dormant", {"channel": "c1"}) is True


def test_query_unknown_key(act):
    assert act.query("foo", {"channel": "c1"}) is None
