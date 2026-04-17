"""LifecyclePlugin 单元测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from plugins.lifecycle.plugin import LifecyclePlugin


@pytest.fixture
def emit_event():
    return AsyncMock()


@pytest.fixture
def plugin(emit_event):
    return LifecyclePlugin(emit_event=emit_event)


# ──────────────────────── 声明 ────────────────────────

def test_handles_commands_declaration(plugin):
    assert set(plugin.handles_commands()) == {"close", "resolve"}


# ──────────────────────── /close ────────────────────────

@pytest.mark.asyncio
async def test_close_emits_channel_closed_event(plugin, emit_event):
    msg = {"channel": "#general", "source": "alice"}
    await plugin.on_command("close", msg)

    emit_event.assert_awaited_once()
    event_name, channel, data = emit_event.call_args[0]
    assert event_name == "channel_closed"
    assert channel == "#general"
    assert data["reason"] == "close"


@pytest.mark.asyncio
async def test_source_passed_to_event_data_close(plugin, emit_event):
    msg = {"channel": "#general", "source": "bob"}
    await plugin.on_command("close", msg)

    _, _, data = emit_event.call_args[0]
    assert data["closed_by"] == "bob"


# ──────────────────────── /resolve ────────────────────────

@pytest.mark.asyncio
async def test_resolve_emits_channel_resolved_then_closed(plugin, emit_event):
    msg = {"channel": "#general", "source": "carol"}
    await plugin.on_command("resolve", msg)

    assert emit_event.await_count == 2

    calls = emit_event.call_args_list
    first_name, first_ch, first_data = calls[0][0]
    second_name, second_ch, second_data = calls[1][0]

    assert first_name == "channel_resolved"
    assert first_ch == "#general"
    assert first_data["outcome"] == "resolved"

    assert second_name == "channel_closed"
    assert second_ch == "#general"
    assert second_data["reason"] == "resolve"


@pytest.mark.asyncio
async def test_source_passed_to_event_data(plugin, emit_event):
    """source 在两个 event 的 data 中都存在。"""
    msg = {"channel": "#general", "source": "carol"}
    await plugin.on_command("resolve", msg)

    calls = emit_event.call_args_list
    _, _, resolved_data = calls[0][0]
    _, _, closed_data = calls[1][0]

    assert resolved_data["resolved_by"] == "carol"
    assert closed_data["closed_by"] == "carol"
