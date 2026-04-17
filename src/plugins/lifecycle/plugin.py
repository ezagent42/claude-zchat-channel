"""lifecycle plugin — /close /resolve 命令。

/close  → emit channel_closed
/resolve → emit channel_resolved + channel_closed

不维护状态；状态统计归 audit plugin。
"""

from __future__ import annotations

from typing import Awaitable, Callable

from channel_server.plugin import BasePlugin


class LifecyclePlugin(BasePlugin):
    """处理 channel 生命周期命令。

    Args:
        emit_event: async (event_name, channel, data) → None
    """

    name = "lifecycle"

    def __init__(
        self,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
    ) -> None:
        self._emit_event = emit_event

    def handles_commands(self) -> list[str]:
        return ["close", "resolve"]

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        channel = msg.get("channel", "")
        source = msg.get("source", "unknown")

        if cmd_name == "close":
            await self._emit_event(
                "channel_closed",
                channel,
                {
                    "channel": channel,
                    "closed_by": source,
                    "reason": "close",
                },
            )

        elif cmd_name == "resolve":
            await self._emit_event(
                "channel_resolved",
                channel,
                {
                    "channel": channel,
                    "resolved_by": source,
                    "outcome": "resolved",
                },
            )
            await self._emit_event(
                "channel_closed",
                channel,
                {
                    "channel": channel,
                    "closed_by": source,
                    "reason": "resolve",
                },
            )
