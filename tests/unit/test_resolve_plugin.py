"""ResolvePlugin 单元测试。"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from plugins.resolve.plugin import ResolvePlugin


@pytest.fixture
def emit_event():
    return AsyncMock()


@pytest.fixture
def plugin(emit_event):
    return ResolvePlugin(config={}, emit_event=emit_event)


def test_handles_resolve_command(plugin):
    assert plugin.handles_commands() == ["resolve"]


@pytest.mark.asyncio
async def test_resolve_emits_channel_resolved(plugin, emit_event):
    msg = {"channel": "#conv-001", "source": "alice"}
    await plugin.on_command("resolve", msg)

    emit_event.assert_awaited_once()
    event_name, channel, data = emit_event.call_args[0]
    assert event_name == "channel_resolved"
    assert channel == "#conv-001"
    assert data["resolved_by"] == "alice"
