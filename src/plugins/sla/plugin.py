"""sla plugin — takeover 后自动 release + 超时告警。

订阅 mode_changed event：
- to=="takeover" → 为该 channel 启一个 timer
- to!="takeover" → 取消 timer（若有）

timer 到期：emit sla_breach event + emit release command
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from channel_server.plugin import BasePlugin

log = logging.getLogger(__name__)


class SlaPlugin(BasePlugin):
    """SLA 守护：takeover 超时后自动回到 copilot。

    Args:
        emit_event:   async (event_name, channel, data) → None
        emit_command: async (cmd_name, channel, args) → None
        timeout_seconds: 超时时长，默认 180 秒
    """

    name = "sla"

    def __init__(
        self,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
        emit_command: Callable[[str, str, dict], Awaitable[None]],
        timeout_seconds: float = 180.0,
    ) -> None:
        self._emit_event = emit_event
        self._emit_command = emit_command
        self._timeout_seconds = timeout_seconds
        self._timers: dict[str, asyncio.Task] = {}

    def handles_commands(self) -> list[str]:
        return []

    async def on_ws_event(self, event: dict) -> None:
        if event.get("event") != "mode_changed":
            return

        data = event.get("data", {})
        channel = event.get("channel", "")
        to_mode = data.get("to", "")

        if to_mode == "takeover":
            await self._start_timer(channel)
        else:
            self._cancel_timer(channel)

    async def _start_timer(self, channel: str) -> None:
        """为 channel 启动（或重置）超时 timer。"""
        self._cancel_timer(channel)
        task = asyncio.create_task(self._timer_task(channel))
        self._timers[channel] = task

    def _cancel_timer(self, channel: str) -> None:
        """取消该 channel 的 timer（若存在）。"""
        task = self._timers.pop(channel, None)
        if task is not None and not task.done():
            task.cancel()

    async def _timer_task(self, channel: str) -> None:
        """sleep 到期后 emit sla_breach + release command。"""
        try:
            await asyncio.sleep(self._timeout_seconds)
        except asyncio.CancelledError:
            return

        log.warning("[sla] channel %r: SLA breach after %ss", channel, self._timeout_seconds)

        try:
            await self._emit_event(
                "sla_breach",
                channel,
                {
                    "channel": channel,
                    "reason": "takeover_timeout",
                    "timeout_seconds": self._timeout_seconds,
                },
            )
        except Exception:
            log.exception("[sla] emit sla_breach failed for channel %r", channel)

        try:
            await self._emit_command("release", channel, {})
        except Exception:
            log.exception("[sla] emit_command release failed for channel %r", channel)
