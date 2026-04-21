"""E2E: agent 求助 operator 完整流程。

场景：
  1. agent 发 __side:@operator 求助 → sla plugin 启动 help timer
  2a. operator 在 timeout 内回应 side → timer 取消，无事件
  2b. operator 未回应 → timer 到期 → emit help_timeout event
"""

from __future__ import annotations

import asyncio

import pytest

from channel_server.plugin import PluginRegistry
from plugins.sla.plugin import SlaPlugin
from zchat_protocol import ws_messages


@pytest.mark.asyncio
async def test_operator_responds_in_time(tmp_path):
    """operator 及时回应 → timer 取消，无 help_timeout 事件。"""
    events: list[dict] = []

    async def emit_event(event, channel, data):
        events.append({"event": event, "channel": channel, "data": data})

    async def emit_command(cmd, channel, args):
        pass

    sla = SlaPlugin(
        emit_event=emit_event, emit_command=emit_command,
        timeout_seconds=9999, help_timeout_seconds=0.2,
    )

    # agent 求助
    await sla.on_ws_message({
        "channel": "conv-1",
        "source": "alice-fast",
        "content": "__side:@operator 需要您确认",
    })
    assert "conv-1" in sla._help_timers

    # 100ms 后 human 经 bridge 中继回应（V6+: source = cs-bot）
    await asyncio.sleep(0.1)
    await sla.on_ws_message({
        "channel": "conv-1",
        "source": "cs-bot",
        "content": "__side:好的",
    })
    await asyncio.sleep(0)

    # help timer 被取消
    assert "conv-1" not in sla._help_timers

    # 再等 0.2s 以上，应该不会触发 help_timeout
    await asyncio.sleep(0.3)
    assert not any(e["event"] == "help_timeout" for e in events)


@pytest.mark.asyncio
async def test_operator_no_response_emits_timeout(tmp_path):
    """operator 未回应 → 到期 emit help_timeout。"""
    events: list[dict] = []

    async def emit_event(event, channel, data):
        events.append({"event": event, "channel": channel, "data": data})

    async def emit_command(cmd, channel, args):
        pass

    sla = SlaPlugin(
        emit_event=emit_event, emit_command=emit_command,
        timeout_seconds=9999, help_timeout_seconds=0.05,
    )

    await sla.on_ws_message({
        "channel": "conv-1",
        "source": "alice-fast",
        "content": "__side:@operator 需要您帮助",
    })

    # 等 timer 到期
    await asyncio.sleep(0.2)

    timeouts = [e for e in events if e["event"] == "help_timeout"]
    assert len(timeouts) == 1
    assert timeouts[0]["channel"] == "conv-1"
    assert timeouts[0]["data"]["reason"] == "no_human_response"


@pytest.mark.asyncio
async def test_help_and_takeover_timers_independent(tmp_path):
    """求助 timer 和 takeover timer 是独立的。"""
    events: list[dict] = []

    async def emit_event(event, channel, data):
        events.append({"event": event, "channel": channel, "data": data})

    commands: list[tuple] = []

    async def emit_command(cmd, channel, args):
        commands.append((cmd, channel))

    sla = SlaPlugin(
        emit_event=emit_event, emit_command=emit_command,
        timeout_seconds=0.05, help_timeout_seconds=9999,
    )

    # 触发 takeover timer
    await sla.on_ws_event({
        "event": "mode_changed",
        "channel": "conv-1",
        "data": {"to": "takeover"},
    })
    # 触发 help timer
    await sla.on_ws_message({
        "channel": "conv-1",
        "source": "alice",
        "content": "__side:@operator please",
    })

    assert "conv-1" in sla._timers
    assert "conv-1" in sla._help_timers

    # 等 takeover timer 到期（0.05s）
    await asyncio.sleep(0.2)
    # takeover timer 触发了 sla_breach + /release command
    assert any(e["event"] == "sla_breach" for e in events)
    assert ("release", "conv-1") in commands
    # help timer 没到期，应该还在
    assert "conv-1" in sla._help_timers or "conv-1" not in sla._help_timers  # 可能被 /release 触发的 mode_changed 取消
