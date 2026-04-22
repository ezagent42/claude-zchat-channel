"""mode plugin — channel mode 状态管理。

命令：/hijack  /release  /copilot
事件：mode_changed
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from channel_server.plugin import BasePlugin


class ModePlugin(BasePlugin):
    """管理每个 channel 的当前模式（"copilot" / "takeover"）。

    /hijack  → copilot → takeover
    /release → takeover → copilot
    /copilot → any → copilot
    """

    name = "mode"

    def __init__(
        self,
        config: dict,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
    ) -> None:
        """V7 config-driven signature. config 为空也可用。"""
        self._modes: dict[str, str] = {}
        self._emit_event = emit_event

    def handles_commands(self) -> list[str]:
        return ["hijack", "release", "copilot"]

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        channel = msg.get("channel", "")
        source = msg.get("source", "unknown")
        old = self._modes.get(channel, "copilot")
        new = "takeover" if cmd_name == "hijack" else "copilot"
        self._modes[channel] = new
        await self._emit_event(
            "mode_changed", channel,
            {"from": old, "to": new, "triggered_by": source, "cmd": cmd_name},
        )

    def query(self, key: str, args: dict | None = None) -> Any:
        if key == "get":
            channel = (args or {}).get("channel", "")
            return self._modes.get(channel, "copilot")
        return None
