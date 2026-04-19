"""CsatPlugin 单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.csat.plugin import CsatPlugin


@pytest.fixture
def emit_event():
    return AsyncMock()


@pytest.fixture
def audit_mock():
    m = MagicMock()
    m.record_csat = MagicMock()
    return m


@pytest.fixture
def csat(emit_event, audit_mock):
    return CsatPlugin(emit_event=emit_event, audit_plugin=audit_mock)


def test_no_commands(csat):
    assert csat.handles_commands() == []


@pytest.mark.asyncio
async def test_channel_resolved_triggers_csat_request(csat, emit_event):
    await csat.on_ws_event({"event": "channel_resolved", "channel": "conv-1", "data": {}})
    emit_event.assert_awaited_once()
    event_name, channel, data = emit_event.call_args[0]
    assert event_name == "csat_request"
    assert channel == "conv-1"


@pytest.mark.asyncio
async def test_other_event_ignored(csat, emit_event):
    await csat.on_ws_event({"event": "mode_changed", "channel": "conv-1", "data": {}})
    emit_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_csat_score_recorded(csat, emit_event, audit_mock):
    await csat.on_ws_message({
        "channel": "conv-1",
        "source": "customer",
        "content": "__csat_score:5",
    })
    audit_mock.record_csat.assert_called_once_with("conv-1", 5)
    emit_event.assert_awaited_once()
    event_name, channel, data = emit_event.call_args[0]
    assert event_name == "csat_recorded"
    assert data["score"] == 5


@pytest.mark.asyncio
async def test_invalid_score_ignored(csat, emit_event, audit_mock):
    await csat.on_ws_message({
        "channel": "conv-1",
        "source": "customer",
        "content": "__csat_score:not_int",
    })
    audit_mock.record_csat.assert_not_called()


@pytest.mark.asyncio
async def test_out_of_range_score_ignored(csat, emit_event, audit_mock):
    await csat.on_ws_message({
        "channel": "conv-1",
        "source": "customer",
        "content": "__csat_score:99",
    })
    audit_mock.record_csat.assert_not_called()


@pytest.mark.asyncio
async def test_non_csat_message_ignored(csat, emit_event, audit_mock):
    await csat.on_ws_message({
        "channel": "conv-1",
        "source": "customer",
        "content": "just a normal message",
    })
    audit_mock.record_csat.assert_not_called()
    emit_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_works_without_audit_plugin(emit_event):
    """audit_plugin=None 时也能 emit event。"""
    csat = CsatPlugin(emit_event=emit_event, audit_plugin=None)
    await csat.on_ws_message({
        "channel": "conv-1",
        "source": "customer",
        "content": "__csat_score:4",
    })
    emit_event.assert_awaited_once()
