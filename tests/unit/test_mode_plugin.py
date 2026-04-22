"""ModePlugin 单元测试。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from plugins.mode.plugin import ModePlugin


@pytest.fixture
def emit_event():
    return AsyncMock()


@pytest.fixture
def plugin(emit_event):
    return ModePlugin(config={}, emit_event=emit_event)


# ──────────────────────── 声明 ────────────────────────

def test_handles_commands_declaration(plugin):
    cmds = plugin.handles_commands()
    assert set(cmds) == {"hijack", "release", "copilot"}


# ──────────────────────── query ────────────────────────

def test_default_mode_is_copilot(plugin):
    result = plugin.query("get", {"channel": "#general"})
    assert result == "copilot"


def test_query_unknown_channel_returns_default(plugin):
    result = plugin.query("get", {"channel": "#nonexistent"})
    assert result == "copilot"


# ──────────────────────── on_command ────────────────────────

@pytest.mark.asyncio
async def test_hijack_sets_takeover(plugin):
    msg = {"channel": "#general", "source": "alice"}
    await plugin.on_command("hijack", msg)
    assert plugin.query("get", {"channel": "#general"}) == "takeover"


@pytest.mark.asyncio
async def test_release_sets_copilot(plugin):
    msg = {"channel": "#general", "source": "alice"}
    await plugin.on_command("hijack", msg)
    await plugin.on_command("release", msg)
    assert plugin.query("get", {"channel": "#general"}) == "copilot"


@pytest.mark.asyncio
async def test_copilot_sets_copilot(plugin):
    msg = {"channel": "#general", "source": "alice"}
    await plugin.on_command("hijack", msg)
    await plugin.on_command("copilot", msg)
    assert plugin.query("get", {"channel": "#general"}) == "copilot"


# ──────────────────────── emit event ────────────────────────

@pytest.mark.asyncio
async def test_mode_changed_event_emitted_with_from_to_triggered_by(plugin, emit_event):
    msg = {"channel": "#general", "source": "alice"}
    await plugin.on_command("hijack", msg)

    emit_event.assert_awaited_once()
    call_args = emit_event.call_args
    event_name, channel, data = call_args[0]

    assert event_name == "mode_changed"
    assert channel == "#general"
    assert data["from"] == "copilot"
    assert data["to"] == "takeover"
    assert data["triggered_by"] == "alice"
    assert data["cmd"] == "hijack"


@pytest.mark.asyncio
async def test_mode_changed_event_emitted_on_release(plugin, emit_event):
    msg = {"channel": "#general", "source": "bob"}
    # 先 hijack，再 release
    await plugin.on_command("hijack", msg)
    emit_event.reset_mock()
    await plugin.on_command("release", msg)

    emit_event.assert_awaited_once()
    _, _, data = emit_event.call_args[0]
    assert data["from"] == "takeover"
    assert data["to"] == "copilot"
