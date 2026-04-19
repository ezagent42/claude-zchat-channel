"""csat plugin — 客户满意度评分链路。

完整流程：
  1. 订阅 channel_resolved 事件 → emit csat_request event（bridge 收到后发评分卡片给客户）
  2. 订阅 on_ws_message 检测 content == "__csat_score:N" → 解析分数 → 调 audit.record_csat(channel, N)
     同时 emit csat_recorded event

不持久化（数据由 audit plugin 保存）。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from channel_server.plugin import BasePlugin

log = logging.getLogger(__name__)

CSAT_SCORE_PREFIX = "__csat_score:"


class CsatPlugin(BasePlugin):
    """资源调节客户评分流程。"""

    name = "csat"

    def __init__(
        self,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
        audit_plugin: Any = None,   # AuditPlugin 引用（懒耦合，可为 None）
    ) -> None:
        self._emit_event = emit_event
        self._audit = audit_plugin

    def handles_commands(self) -> list[str]:
        return []

    async def on_ws_event(self, event: dict) -> None:
        name = event.get("event") or ""
        channel = event.get("channel") or ""
        if name == "channel_resolved" and channel:
            # 请求 bridge 发评分卡片
            try:
                await self._emit_event("csat_request", channel, {})
            except Exception:
                log.exception("[csat] emit csat_request failed")

    async def on_ws_message(self, msg: dict) -> None:
        content = msg.get("content") or ""
        channel = msg.get("channel") or ""
        if not content.startswith(CSAT_SCORE_PREFIX) or not channel:
            return
        raw_score = content[len(CSAT_SCORE_PREFIX):].strip()
        try:
            score = int(raw_score)
        except ValueError:
            log.warning("[csat] invalid score in content: %s", content)
            return
        if not (1 <= score <= 5):
            log.warning("[csat] out-of-range score: %d", score)
            return

        # 写入 audit
        if self._audit is not None:
            try:
                self._audit.record_csat(channel, score)
            except Exception:
                log.exception("[csat] audit.record_csat failed")

        # emit csat_recorded event（方便其他 plugin 订阅）
        try:
            await self._emit_event(
                "csat_recorded",
                channel,
                {"score": score, "source": msg.get("source", "customer")},
            )
        except Exception:
            log.exception("[csat] emit csat_recorded failed")
