"""resolve plugin — 对话结束事件触发器。

命令：/resolve
事件：channel_resolved

/resolve 只 emit 事件，不做持久化。
后续可在 on_ws_event 中挂载持久化逻辑（写数据库、清理 routing、通知 agent 退出等）。
"""

from __future__ import annotations

from typing import Awaitable, Callable

from channel_server.plugin import BasePlugin


class ResolvePlugin(BasePlugin):
    """/resolve → emit channel_resolved 事件。"""

    name = "resolve"

    def __init__(
        self,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
    ) -> None:
        self._emit_event = emit_event

    def handles_commands(self) -> list[str]:
        return ["resolve"]

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        channel = msg.get("channel", "")
        source = msg.get("source", "unknown")
        await self._emit_event(
            "channel_resolved",
            channel,
            {"resolved_by": source},
        )
