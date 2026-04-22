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


def make_plugin(emit_event, emit_command, timeout=0.05, help_timeout=None):
    """Helper: construct SlaPlugin with V7 config-driven signature."""
    config = {"takeover_timeout": timeout}
    if help_timeout is not None:
        config["help_timeout"] = help_timeout
    return SlaPlugin(
        config=config,
        emit_event=emit_event,
        emit_command=emit_command,
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


# ──────────────────────── Timer 2: 求助等待 ────────────────────────

@pytest.mark.asyncio
async def test_side_operator_mention_starts_help_timer(emit_event, emit_command):
    plugin = SlaPlugin(
        config={"takeover_timeout": 9999, "help_timeout": 9999},
        emit_event=emit_event,
        emit_command=emit_command,
    )
    # agent 发 side 消息 @operator
    msg = {
        "type": "message",
        "channel": "#conv-1",
        "source": "alice-fast-001",
        "content": "__side:@operator 需要您确认",
    }
    await plugin.on_ws_message(msg)

    assert "#conv-1" in plugin._help_timers
    assert not plugin._help_timers["#conv-1"].done()
    plugin._cancel_help_timer("#conv-1")


@pytest.mark.asyncio
async def test_side_admin_mention_also_triggers(emit_event, emit_command):
    plugin = SlaPlugin(
        config={"takeover_timeout": 9999, "help_timeout": 9999},
        emit_event=emit_event, emit_command=emit_command,
    )
    msg = {
        "type": "message",
        "channel": "#c",
        "source": "alice-fast",
        "content": "__side:@admin 请注意这里",
    }
    await plugin.on_ws_message(msg)
    assert "#c" in plugin._help_timers
    plugin._cancel_help_timer("#c")


@pytest.mark.asyncio
async def test_agent_to_agent_side_with_quoted_operator_does_not_trigger(
    emit_event, emit_command
):
    """deep 在 side 里建议 fast 走 @operator 流程（agent-to-agent）—— 不应触发求助。

    回归 2026-04-21 false-positive：首个 @-mention 是其它 agent nick 而非 help marker
    时，sla 不该启 help timer。
    """
    plugin = SlaPlugin(
        config={"takeover_timeout": 9999, "help_timeout": 9999},
        emit_event=emit_event, emit_command=emit_command,
    )
    msg = {
        "type": "message",
        "channel": "#c",
        "source": "yaosh-deep-001",
        "content": (
            "__side:@yaosh-fast-001 查不到订单 #123，建议走 @operator 流程。"
            "edit_of=abc 的占位由你接管。"
        ),
    }
    await plugin.on_ws_message(msg)
    assert "#c" not in plugin._help_timers


@pytest.mark.asyncio
async def test_non_side_msg_ignored_for_help(emit_event, emit_command):
    plugin = SlaPlugin(
        config={"takeover_timeout": 9999, "help_timeout": 9999},
        emit_event=emit_event, emit_command=emit_command,
    )
    msg = {
        "channel": "#c",
        "source": "alice-fast",
        "content": "@operator help",  # 无 __side: 前缀
    }
    await plugin.on_ws_message(msg)
    assert "#c" not in plugin._help_timers


@pytest.mark.asyncio
async def test_human_relay_side_cancels_help_timer(emit_event, emit_command):
    plugin = SlaPlugin(
        config={"takeover_timeout": 9999, "help_timeout": 9999},
        emit_event=emit_event, emit_command=emit_command,
    )
    # agent 求助
    await plugin.on_ws_message({
        "channel": "#c",
        "source": "alice-fast",
        "content": "__side:@operator 请确认",
    })
    assert "#c" in plugin._help_timers

    # 人工通过 bridge 中继回应（source = cs-bot）
    await plugin.on_ws_message({
        "channel": "#c",
        "source": "cs-bot",
        "content": "__side:好的我来处理",
    })
    # yield 给 event loop
    import asyncio as _aio
    await _aio.sleep(0)
    assert "#c" not in plugin._help_timers


@pytest.mark.asyncio
async def test_help_timer_expiry_emits_help_timeout(emit_event, emit_command):
    plugin = SlaPlugin(
        config={"takeover_timeout": 9999, "help_timeout": 0.05},
        emit_event=emit_event, emit_command=emit_command,
    )
    await plugin.on_ws_message({
        "channel": "#c",
        "source": "alice-fast",
        "content": "__side:@operator 求助",
    })
    await asyncio.sleep(0.2)

    help_calls = [
        c for c in emit_event.call_args_list
        if c[0][0] == "help_timeout"
    ]
    assert len(help_calls) == 1
    _, channel, data = help_calls[0][0]
    assert channel == "#c"
    assert data["reason"] == "no_human_response"
    assert data["text"] == "@operator 求助"


@pytest.mark.asyncio
async def test_release_to_copilot_cancels_help_timer(emit_event, emit_command):
    """mode 切回 copilot 时，help timer 也应取消（防止泄漏）。"""
    plugin = SlaPlugin(
        config={"takeover_timeout": 9999, "help_timeout": 9999},
        emit_event=emit_event, emit_command=emit_command,
    )
    await plugin.on_ws_message({
        "channel": "#c",
        "source": "alice-fast",
        "content": "__side:@operator help",
    })
    assert "#c" in plugin._help_timers

    # mode_changed to copilot
    await plugin.on_ws_event(make_mode_changed_event("#c", "copilot"))
    import asyncio as _aio
    await _aio.sleep(0)
    assert "#c" not in plugin._help_timers
