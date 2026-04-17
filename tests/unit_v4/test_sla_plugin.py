"""SlaPlugin 单元测试。"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock

from plugins.sla.plugin import SlaPlugin


@pytest.fixture
def emit_event():
    return AsyncMock()


@pytest.fixture
def emit_command():
    return AsyncMock()


def make_plugin(emit_event, emit_command, timeout=0.05):
    return SlaPlugin(
        emit_event=emit_event,
        emit_command=emit_command,
        timeout_seconds=timeout,
    )


def make_mode_changed_event(channel: str, to_mode: str) -> dict:
    return {
        "type": "event",
        "event": "mode_changed",
        "channel": channel,
        "data": {"from": "copilot", "to": to_mode, "triggered_by": "alice"},
    }


# ──────────────────────── 声明 ────────────────────────

def test_handles_commands_is_empty(emit_event, emit_command):
    plugin = make_plugin(emit_event, emit_command)
    assert plugin.handles_commands() == []


# ──────────────────────── timer 启动 / 取消 ────────────────────────

@pytest.mark.asyncio
async def test_mode_changed_to_takeover_starts_timer(emit_event, emit_command):
    plugin = make_plugin(emit_event, emit_command, timeout=9999)
    event = make_mode_changed_event("#general", "takeover")
    await plugin.on_ws_event(event)

    assert "#general" in plugin._timers
    assert not plugin._timers["#general"].done()

    # 清理
    plugin._cancel_timer("#general")


@pytest.mark.asyncio
async def test_mode_changed_to_copilot_cancels_timer(emit_event, emit_command):
    plugin = make_plugin(emit_event, emit_command, timeout=9999)

    # 先启动
    await plugin.on_ws_event(make_mode_changed_event("#general", "takeover"))
    task = plugin._timers["#general"]
    assert not task.done()

    # 再 release
    await plugin.on_ws_event(make_mode_changed_event("#general", "copilot"))
    # task.cancel() 已调用，但需要 yield 让事件循环处理取消
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    assert "#general" not in plugin._timers


# ──────────────────────── timer 到期 ────────────────────────

@pytest.mark.asyncio
async def test_timer_expiry_emits_sla_breach_event(emit_event, emit_command):
    plugin = make_plugin(emit_event, emit_command, timeout=0.05)
    await plugin.on_ws_event(make_mode_changed_event("#shop", "takeover"))

    # 等待 timer 到期（0.05s + 余量）
    await asyncio.sleep(0.2)

    # 检查 sla_breach
    sla_calls = [
        c for c in emit_event.call_args_list
        if c[0][0] == "sla_breach"
    ]
    assert len(sla_calls) == 1
    _, channel, data = sla_calls[0][0]
    assert channel == "#shop"
    assert data["reason"] == "takeover_timeout"
    assert data["timeout_seconds"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_timer_expiry_emits_release_command(emit_event, emit_command):
    plugin = make_plugin(emit_event, emit_command, timeout=0.05)
    await plugin.on_ws_event(make_mode_changed_event("#shop", "takeover"))

    await asyncio.sleep(0.2)

    emit_command.assert_awaited_once()
    cmd_name, channel, _args = emit_command.call_args[0]
    assert cmd_name == "release"
    assert channel == "#shop"


# ──────────────────────── 多 channel 独立 ────────────────────────

@pytest.mark.asyncio
async def test_multiple_channels_independent_timers(emit_event, emit_command):
    plugin = make_plugin(emit_event, emit_command, timeout=9999)

    await plugin.on_ws_event(make_mode_changed_event("#ch1", "takeover"))
    await plugin.on_ws_event(make_mode_changed_event("#ch2", "takeover"))

    assert "#ch1" in plugin._timers
    assert "#ch2" in plugin._timers
    # 两个 timer 是不同对象
    assert plugin._timers["#ch1"] is not plugin._timers["#ch2"]

    plugin._cancel_timer("#ch1")
    plugin._cancel_timer("#ch2")
