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
    return CsatPlugin(config={}, emit_event=emit_event, audit=audit_mock)


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
    await csat.on_ws_event({
        "event": "csat_score",
        "channel": "conv-1",
        "data": {"score": 5, "source": "customer"},
    })
    audit_mock.record_csat.assert_called_once_with("conv-1", 5)
    emit_event.assert_awaited_once()
    event_name, channel, data = emit_event.call_args[0]
    assert event_name == "csat_recorded"
    assert data["score"] == 5


@pytest.mark.asyncio
async def test_invalid_score_ignored(csat, emit_event, audit_mock):
    await csat.on_ws_event({
        "event": "csat_score",
        "channel": "conv-1",
        "data": {"score": "not_int", "source": "customer"},
    })
    audit_mock.record_csat.assert_not_called()


@pytest.mark.asyncio
async def test_out_of_range_score_ignored(csat, emit_event, audit_mock):
    await csat.on_ws_event({
        "event": "csat_score",
        "channel": "conv-1",
        "data": {"score": 99, "source": "customer"},
    })
    audit_mock.record_csat.assert_not_called()


@pytest.mark.asyncio
async def test_non_csat_event_ignored(csat, emit_event, audit_mock):
    """非 csat_score 事件不应触发记录。"""
    await csat.on_ws_event({
        "event": "mode_changed",
        "channel": "conv-1",
        "data": {"to": "takeover"},
    })
    audit_mock.record_csat.assert_not_called()
    emit_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_works_without_audit_plugin(emit_event):
    """audit_plugin=None 时也能 emit event。"""
    csat = CsatPlugin(config={}, emit_event=emit_event, audit=None)
    await csat.on_ws_event({
        "event": "csat_score",
        "channel": "conv-1",
        "data": {"score": 4, "source": "customer"},
    })
    emit_event.assert_awaited_once()
