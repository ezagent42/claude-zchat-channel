"""csat plugin — 客户满意度评分链路。

完整流程：
  1. 订阅 channel_resolved 事件 → emit csat_request event（bridge 发评分卡片给客户）
  2. 订阅 on_ws_event 的 csat_score（bridge 客户点击后 emit）→ audit.record_csat + emit csat_recorded

不持久化（数据由 audit plugin 保存）。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from channel_server.plugin import BasePlugin

log = logging.getLogger(__name__)


class CsatPlugin(BasePlugin):
    """资源调节客户评分流程。"""

    name = "csat"

    def __init__(
        self,
        config: dict,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
        *,
        audit: Any = None,   # AuditPlugin 引用，loader 通过 signature-driven DI 注入
    ) -> None:
        """V7 config-driven signature.

        config: 当前无业务参数。
        audit: kw-only，由 plugin_loader 按参数名自动从 registry.get_plugin("audit") 注入。
               audit plugin 未启用时为 None，on_ws_event 里的 `if self._audit:` 分支已处理。
        """
        self._emit_event = emit_event
        self._audit = audit

    def handles_commands(self) -> list[str]:
        return []

    async def on_ws_event(self, event: dict) -> None:
        name = event.get("event") or ""
        channel = event.get("channel") or ""
        if not channel:
            return

        if name == "channel_resolved":
            # 请求 bridge 发评分卡片
            try:
                await self._emit_event("csat_request", channel, {})
            except Exception:
                log.exception("[csat] emit csat_request failed")
            return

        if name == "csat_score":
            data = event.get("data") or {}
            try:
                score = int(data.get("score"))
            except (TypeError, ValueError):
                log.warning("[csat] invalid score in event: %s", data)
                return
            if not (1 <= score <= 5):
                log.warning("[csat] out-of-range score: %d", score)
                return

            if self._audit is not None:
                try:
                    self._audit.record_csat(channel, score)
                except Exception:
                    log.exception("[csat] audit.record_csat failed")

            try:
                await self._emit_event(
                    "csat_recorded",
                    channel,
                    {"score": score, "source": data.get("source", "customer")},
                )
            except Exception:
                log.exception("[csat] emit csat_recorded failed")
