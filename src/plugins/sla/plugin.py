"""sla plugin — SLA 守护（两种 timer）。

Timer 1：takeover 超时
  订阅 mode_changed：to=="takeover" → 启动 timer；to!="takeover" → 取消
  到期：emit sla_breach + emit /release command

Timer 2：求助等待（Phase 10）
  订阅 on_ws_message：检测 __side: 消息中的 @operator/@人工/@admin pattern
    → 启动 help_wait timer（独立于 takeover timer）
  订阅 on_ws_message：检测到同 channel 内 operator 的 side 回复 → 取消 timer
  到期：emit help_timeout event（agent 通过 IRC sys 收到，按 soul.md 发安抚消息）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from channel_server.plugin import BasePlugin
from zchat_protocol import irc_encoding

log = logging.getLogger(__name__)


# 求助 pattern — 在 __side: 内容中出现任一即视为 agent 求助
HELP_MENTION_PATTERNS = ("@operator", "@人工", "@admin", "@客服")

# operator 源标识（source 字段包含任一即认为是 operator 回应）
OPERATOR_SOURCE_MARKERS = ("operator", "ou_")  # 飞书 open_id 以 ou_ 开头


class SlaPlugin(BasePlugin):
    """SLA 守护 plugin。"""

    name = "sla"

    def __init__(
        self,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
        emit_command: Callable[[str, str, dict], Awaitable[None]],
        timeout_seconds: float = 180.0,
        help_timeout_seconds: float | None = None,
    ) -> None:
        self._emit_event = emit_event
        self._emit_command = emit_command
        self._timeout_seconds = timeout_seconds
        self._help_timeout_seconds = help_timeout_seconds or timeout_seconds
        self._timers: dict[str, asyncio.Task] = {}
        self._help_timers: dict[str, asyncio.Task] = {}

    def handles_commands(self) -> list[str]:
        return []

    # ------------------------------------------------------------------
    # Timer 1: takeover 超时
    # ------------------------------------------------------------------

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
            # mode 恢复 copilot 时，求助 timer 也取消（如果有）
            self._cancel_help_timer(channel)

    async def _start_timer(self, channel: str) -> None:
        """启动（或重置）takeover 超时 timer。"""
        self._cancel_timer(channel)
        task = asyncio.create_task(self._timer_task(channel))
        self._timers[channel] = task

    def _cancel_timer(self, channel: str) -> None:
        task = self._timers.pop(channel, None)
        if task is not None and not task.done():
            task.cancel()

    async def _timer_task(self, channel: str) -> None:
        """takeover 超时 → sla_breach + /release。"""
        try:
            await asyncio.sleep(self._timeout_seconds)
        except asyncio.CancelledError:
            return

        log.warning("[sla] channel %r: takeover SLA breach after %ss", channel, self._timeout_seconds)

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

    # ------------------------------------------------------------------
    # Timer 2: 求助等待（agent 发 __side:@operator → 启动 timer）
    # ------------------------------------------------------------------

    async def on_ws_message(self, msg: dict) -> None:
        """检测 side 消息：
        - 含 @operator/@人工/@admin → agent 求助 → 启动 help timer
        - source 看起来是 operator → 取消 help timer（已响应）
        """
        content = msg.get("content") or ""
        channel = msg.get("channel") or ""
        source = (msg.get("source") or "").lower()
        if not channel or not content:
            return

        parsed = irc_encoding.parse(content)
        if parsed.get("kind") != "side":
            return
        text = parsed.get("text") or ""

        if self._looks_like_operator(source):
            # operator 回应 → 取消 help timer（如果有）
            self._cancel_help_timer(channel)
            return

        if any(marker in text for marker in HELP_MENTION_PATTERNS):
            # agent 发起求助
            await self._start_help_timer(channel)

    async def _start_help_timer(self, channel: str) -> None:
        self._cancel_help_timer(channel)
        task = asyncio.create_task(self._help_timer_task(channel))
        self._help_timers[channel] = task
        log.info("[sla] channel %r: help wait timer started (%ss)", channel, self._help_timeout_seconds)

    def _cancel_help_timer(self, channel: str) -> None:
        task = self._help_timers.pop(channel, None)
        if task is not None and not task.done():
            task.cancel()

    async def _help_timer_task(self, channel: str) -> None:
        """求助超时 → emit help_timeout event。"""
        try:
            await asyncio.sleep(self._help_timeout_seconds)
        except asyncio.CancelledError:
            return

        log.warning("[sla] channel %r: help wait SLA breach after %ss", channel, self._help_timeout_seconds)

        try:
            await self._emit_event(
                "help_timeout",
                channel,
                {
                    "channel": channel,
                    "reason": "operator_no_response",
                    "timeout_seconds": self._help_timeout_seconds,
                },
            )
        except Exception:
            log.exception("[sla] emit help_timeout failed for channel %r", channel)

    @staticmethod
    def _looks_like_operator(source: str) -> bool:
        """source 是否为 operator（基于约定）。"""
        if not source:
            return False
        return any(m in source for m in OPERATOR_SOURCE_MARKERS)
