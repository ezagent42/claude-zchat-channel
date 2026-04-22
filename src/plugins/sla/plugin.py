"""sla plugin — SLA 守护（两种 timer）。

Timer 1：takeover 超时
  订阅 mode_changed：to=="takeover" → 启动 timer；to!="takeover" → 取消
  到期：emit sla_breach + emit /release command

Timer 2：求助等待
  订阅 on_ws_message：检测 __side: 消息中的人工求助 marker
    → emit help_requested event + 启动 help_wait timer（独立于 takeover timer）
  订阅 on_ws_message：检测同 channel 内 bridge-relayed 的 side 回复 → 取消 timer
  到期：emit help_timeout event（带原求助文本）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable

from channel_server.plugin import BasePlugin
from zchat_protocol import irc_encoding

log = logging.getLogger(__name__)


# 求助标记 — 在 __side: 内容中出现任一即视为 agent 申请人工介入。
# 这些是 agent 模板里约定的 trigger 文本，不是协议字段。
HELP_REQUEST_MARKERS = ("@operator", "@人工", "@admin", "@客服")

# Bridge / 服务源 IRC nick — source 字段含任一即视为"人类经由 bridge 中继"，
# 用于在 help_requested 等待期间识别"人工已响应"，取消 timer。
HUMAN_RELAY_SOURCE_MARKERS = ("cs-bot",)


# 匹配 "@" 紧跟的**第一个** token（到空白为止）
_FIRST_AT_RE = re.compile(r"@\S+")


def _first_at_is_help_marker(text: str) -> bool:
    """True 仅当 text 第一个 @-mention 是 HELP_REQUEST_MARKERS 之一。

    避免 false positive：agent 之间互相 quote marker（如 deep 在 side 里
    说"@yaosh-fast-001 ... 建议走 @operator 流程"）不应触发求助。
    """
    if not text:
        return False
    m = _FIRST_AT_RE.search(text)
    if not m:
        return False
    first_at = m.group(0)
    return any(first_at.startswith(marker) for marker in HELP_REQUEST_MARKERS)


class SlaPlugin(BasePlugin):
    """SLA 守护 plugin。"""

    name = "sla"

    def __init__(
        self,
        config: dict,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
        emit_command: Callable[[str, str, dict], Awaitable[None]],
    ) -> None:
        """V7 config-driven signature.

        config:
          - takeover_timeout (default 180s): mode=takeover 后多久自动 /release
          - help_timeout (default = takeover_timeout): @operator 求助后等多久 emit help_timeout
        """
        self._emit_event = emit_event
        self._emit_command = emit_command
        self._timeout_seconds = float(config.get("takeover_timeout", 180.0))
        self._help_timeout_seconds = float(config.get("help_timeout") or self._timeout_seconds)
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
    # Timer 2: 求助等待（agent 在 side 消息里写 HELP_REQUEST_MARKERS → 启动 timer）
    # ------------------------------------------------------------------

    async def on_ws_message(self, msg: dict) -> None:
        """检测 side 消息：
        - 含 HELP_REQUEST_MARKERS → agent 申请人工 → emit help_requested + 启动 help timer
        - source 是 bridge 中继（cs-bot 等）→ 视作人工已响应 → 取消 help timer
        """
        content = msg.get("content") or ""
        channel = msg.get("channel") or ""
        source = msg.get("source") or ""
        if not channel or not content:
            return

        parsed = irc_encoding.parse(content)
        if parsed.get("kind") != "side":
            return
        text = parsed.get("text") or ""

        if self._is_human_relay(source.lower()):
            # bridge 转发的 side（人工通过 squad thread 写的建议）→ 取消 help timer
            self._cancel_help_timer(channel)
            return

        if _first_at_is_help_marker(text):
            # agent 发起求助：emit 立即通知 + 启动 timer
            try:
                await self._emit_event(
                    "help_requested",
                    channel,
                    {
                        "channel": channel,
                        "text": text,
                        "requesting_source": source,
                    },
                )
            except Exception:
                log.exception("[sla] emit help_requested failed for %r", channel)
            await self._start_help_timer(channel, text)

    async def _start_help_timer(self, channel: str, request_text: str = "") -> None:
        self._cancel_help_timer(channel)
        task = asyncio.create_task(self._help_timer_task(channel, request_text))
        self._help_timers[channel] = task
        log.info("[sla] channel %r: help wait timer started (%ss)", channel, self._help_timeout_seconds)

    def _cancel_help_timer(self, channel: str) -> None:
        task = self._help_timers.pop(channel, None)
        if task is not None and not task.done():
            task.cancel()

    async def _help_timer_task(self, channel: str, request_text: str) -> None:
        """求助超时 → emit help_timeout event（payload 带原求助文本）。"""
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
                    "reason": "no_human_response",
                    "timeout_seconds": self._help_timeout_seconds,
                    "text": request_text,
                },
            )
        except Exception:
            log.exception("[sla] emit help_timeout failed for channel %r", channel)

    @staticmethod
    def _is_human_relay(source: str) -> bool:
        """source 是否是 bridge 服务源（人工通过 bridge 转发回来的 side）。"""
        if not source:
            return False
        return any(m in source for m in HUMAN_RELAY_SOURCE_MARKERS)
