"""AuditPlugin 单元测试。"""

from __future__ import annotations

import pytest

from plugins.audit.plugin import AuditPlugin


@pytest.fixture
def plugin():
    return AuditPlugin()


def make_mode_changed_event(channel: str, to_mode: str) -> dict:
    return {
        "type": "event",
        "event": "mode_changed",
        "channel": channel,
        "data": {"from": "copilot", "to": to_mode},
    }


def make_channel_closed_event(channel: str) -> dict:
    return {
        "type": "event",
        "event": "channel_closed",
        "channel": channel,
        "data": {"closed_by": "alice", "reason": "close"},
    }


# ──────────────────────── 声明 ────────────────────────

def test_handles_commands_is_empty(plugin):
    assert plugin.handles_commands() == []


# ──────────────────────── takeover_count ────────────────────────

@pytest.mark.asyncio
async def test_takeover_count_increments_on_mode_change_to_takeover(plugin):
    await plugin.on_ws_event(make_mode_changed_event("#general", "takeover"))
    assert plugin.query("takeover_count", {"channel": "#general"}) == 1

    await plugin.on_ws_event(make_mode_changed_event("#general", "takeover"))
    assert plugin.query("takeover_count", {"channel": "#general"}) == 2


@pytest.mark.asyncio
async def test_takeover_count_not_increments_on_other_transitions(plugin):
    await plugin.on_ws_event(make_mode_changed_event("#general", "copilot"))
    assert plugin.query("takeover_count", {"channel": "#general"}) == 0


# ──────────────────────── resolved_count ────────────────────────

@pytest.mark.asyncio
async def test_resolved_count_increments_on_channel_closed(plugin):
    await plugin.on_ws_event(make_channel_closed_event("#general"))
    assert plugin.query("resolved_count", {"channel": "#general"}) == 1

    await plugin.on_ws_event(make_channel_closed_event("#general"))
    assert plugin.query("resolved_count", {"channel": "#general"}) == 2


# ──────────────────────── recent_events ────────────────────────

@pytest.mark.asyncio
async def test_recent_events_returns_last_n(plugin):
    for i in range(5):
        await plugin.on_ws_event(
            {"type": "event", "event": f"event_{i}", "channel": "#ch", "data": {}}
        )

    result = plugin.query("recent_events", {"limit": 3})
    assert len(result) == 3
    # 最后 3 条
    assert result[-1]["event"] == "event_4"
    assert result[0]["event"] == "event_2"


@pytest.mark.asyncio
async def test_recent_events_limit_default(plugin):
    """不传 limit 时返回全部。"""
    for i in range(5):
        await plugin.on_ws_event(
            {"type": "event", "event": f"ev_{i}", "channel": "#ch", "data": {}}
        )

    result = plugin.query("recent_events", {})
    assert len(result) == 5


# ──────────────────────── status ────────────────────────

@pytest.mark.asyncio
async def test_status_aggregates_per_channel(plugin):
    await plugin.on_ws_event(make_mode_changed_event("#ch1", "takeover"))
    await plugin.on_ws_event(make_mode_changed_event("#ch1", "takeover"))
    await plugin.on_ws_event(make_channel_closed_event("#ch1"))
    await plugin.on_ws_event(make_mode_changed_event("#ch2", "takeover"))

    status = plugin.query("status")
    assert status["#ch1"]["takeover_count"] == 2
    assert status["#ch1"]["resolved_count"] == 1
    assert status["#ch2"]["takeover_count"] == 1
    assert status["#ch2"]["resolved_count"] == 0


# ──────────────────────── 未知 query ────────────────────────

def test_query_unknown_returns_none(plugin):
    result = plugin.query("nonexistent_key")
    assert result is None
