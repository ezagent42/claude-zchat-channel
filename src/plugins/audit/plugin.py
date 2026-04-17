"""audit plugin — 订阅所有 event 维护计数 + query API。

计数：
- takeover_count[channel]: 看到 mode_changed(to=takeover) 时 +1
- resolved_count[channel]: 看到 channel_closed event 时 +1

近期 event 日志：最多保留 max_events 条（FIFO）

query API：
- query("takeover_count", {"channel": ch}) → int
- query("resolved_count", {"channel": ch}) → int
- query("recent_events", {"limit": n}) → list[dict]
- query("status") → dict[channel, {"takeover_count": ..., "resolved_count": ...}]
"""

from __future__ import annotations

import collections
from typing import Any

from channel_server.plugin import BasePlugin


class AuditPlugin(BasePlugin):
    """审计插件：无状态写入，仅观察 + 统计。

    Args:
        max_events: 最近 event 日志容量，默认 1000
    """

    name = "audit"

    def __init__(self, max_events: int = 1000) -> None:
        self._takeover_count: dict[str, int] = {}
        self._resolved_count: dict[str, int] = {}
        self._events: collections.deque[dict] = collections.deque(maxlen=max_events)

    def handles_commands(self) -> list[str]:
        return []

    async def on_ws_event(self, event: dict) -> None:
        # 记录所有 event
        self._events.append(event)

        event_name = event.get("event", "")
        channel = event.get("channel", "")

        if event_name == "mode_changed":
            data = event.get("data", {})
            if data.get("to") == "takeover":
                self._takeover_count[channel] = self._takeover_count.get(channel, 0) + 1

        elif event_name == "channel_closed":
            self._resolved_count[channel] = self._resolved_count.get(channel, 0) + 1

    def query(self, key: str, args: dict | None = None) -> Any:
        args = args or {}

        if key == "takeover_count":
            channel = args.get("channel", "")
            return self._takeover_count.get(channel, 0)

        if key == "resolved_count":
            channel = args.get("channel", "")
            return self._resolved_count.get(channel, 0)

        if key == "recent_events":
            limit = args.get("limit", len(self._events))
            events_list = list(self._events)
            return events_list[-limit:] if limit else []

        if key == "status":
            # 合并所有 channel 的计数
            all_channels = set(self._takeover_count) | set(self._resolved_count)
            return {
                ch: {
                    "takeover_count": self._takeover_count.get(ch, 0),
                    "resolved_count": self._resolved_count.get(ch, 0),
                }
                for ch in all_channels
            }

        return None
