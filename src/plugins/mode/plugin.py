"""mode plugin — channel mode 状态管理。

支持命令：/hijack  /release  /copilot
emit event：mode_changed
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from channel_server.plugin import BasePlugin


class ModePlugin(BasePlugin):
    """管理每个 channel 的当前模式（"copilot" / "takeover"）。

    Args:
        emit_event: async (event_name, channel, data) → None
    """

    name = "mode"

    def __init__(
        self,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
    ) -> None:
        self._modes: dict[str, str] = {}
        self._emit_event = emit_event

    def handles_commands(self) -> list[str]:
        return ["hijack", "release", "copilot"]

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        channel = msg.get("channel", "")
        old = self._modes.get(channel, "copilot")
        new = "takeover" if cmd_name == "hijack" else "copilot"
        self._modes[channel] = new
        await self._emit_event(
            "mode_changed",
            channel,
            {
                "from": old,
                "to": new,
                "triggered_by": msg.get("source", "unknown"),
                "cmd": cmd_name,
            },
        )

    def query(self, key: str, args: dict | None = None) -> Any:
        if key == "get":
            channel = (args or {}).get("channel", "")
            return self._modes.get(channel, "copilot")
        return None
