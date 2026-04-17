"""Channel activation plugin — 监测 channel 消息，客户回访时自动拉起 agent。

场景：
  - 客户 close/resolve 后，agent 进程可能已 stop（workspace 保留）
  - 客户一周后重来，消息进入同 channel，但 agent nick 不在 IRC
  - 此插件订阅消息流，检测到目标 agent 缺席时触发 re-dispatch
  - agent 重启后自动读取保留的 .claude 历史，上下文延续

持久化：
  ~/.zchat/.../activation-state.json
  {
    "channels": {
      "ch-群A": {
        "last_activity": "2026-04-17T10:00:00Z",
        "last_closed_at": "2026-04-10T15:00:00Z",
        "agent_dispatched": true
      }
    }
  }
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


class ActivationPlugin:
    """订阅 message + channel_closed event，持久化 channel 活跃状态。"""

    name = "activation"

    def __init__(
        self,
        state_file: str | Path,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
    ) -> None:
        self._state_file = Path(state_file)
        self._emit_event = emit_event
        self._state: dict[str, Any] = self._load()

    def handles_commands(self) -> list[str]:
        return []

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self._state_file.exists():
            return {"channels": {}}
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("activation state load failed: %s", e)
            return {"channels": {}}

    def _save(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._state_file)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _channel_state(self, channel: str) -> dict[str, Any]:
        return self._state["channels"].setdefault(channel, {})

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def on_ws_message(self, msg: dict) -> None:
        """每条 message 进来记一次活跃时间，判断是否需要 re-dispatch。"""
        channel = msg.get("channel", "")
        if not channel:
            return

        ch_state = self._channel_state(channel)
        was_closed = ch_state.get("last_closed_at") and not ch_state.get("agent_dispatched")
        ch_state["last_activity"] = self._now()
        self._save()

        if was_closed:
            # TODO (user): 决定客户回访的处理策略
            await self._handle_returning_customer(channel, msg, ch_state)

    async def on_ws_event(self, event: dict) -> None:
        """订阅 channel_closed 记录关闭时间。"""
        event_name = event.get("event", "")
        channel = event.get("channel", "")
        if not channel:
            return

        if event_name == "channel_closed":
            ch_state = self._channel_state(channel)
            ch_state["last_closed_at"] = self._now()
            ch_state["agent_dispatched"] = False
            self._save()
        elif event_name == "agent_dispatched":
            ch_state = self._channel_state(channel)
            ch_state["agent_dispatched"] = True
            self._save()

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        pass  # 不接管命令

    def query(self, key: str, args: dict | None = None) -> Any:
        if key == "last_activity":
            ch = (args or {}).get("channel", "")
            return self._channel_state(ch).get("last_activity")
        if key == "last_closed":
            ch = (args or {}).get("channel", "")
            return self._channel_state(ch).get("last_closed_at")
        if key == "is_dormant":
            # 是否处于"关闭过且未重新 dispatch"状态
            ch = (args or {}).get("channel", "")
            s = self._channel_state(ch)
            return bool(s.get("last_closed_at") and not s.get("agent_dispatched"))
        return None

    # ------------------------------------------------------------------
    # 客户回访策略（交给用户实现）
    # ------------------------------------------------------------------

    async def _handle_returning_customer(
        self,
        channel: str,
        msg: dict,
        ch_state: dict[str, Any],
    ) -> None:
        """
        客户回访到一个已关闭的 channel。决定做什么。

        可选策略：
          1. 自动 emit command /dispatch（auto-revive 默认 agent）
             优点：客户无感知，体验连续
             缺点：运营失去"哪些客户回访"的可见性；误派单风险

          2. 只 emit event 告警（customer_returned），让 squad 人工处理
             优点：operator 主动欢迎客户，控制感强
             缺点：客户等待

          3. 视关闭时长决策：
             - < 1 小时：自动 revive（简单追问场景）
             - 1 小时 ~ 7 天：通知 squad + auto-dispatch（标记为回访）
             - > 7 天：仅通知，operator 审批后再 dispatch

        TODO: 在下方实现您的策略。需要的数据已在 ch_state：
          - ch_state["last_closed_at"]: 关闭时间（ISO 字符串）
          - ch_state["last_activity"]: 当前消息时间
          - msg["source"]: 客户身份
          - msg["content"]: 客户说了什么
        """
        # 占位：仅 emit 告警 event，不自动 dispatch
        await self._emit_event(
            "customer_returned",
            channel,
            {
                "closed_at": ch_state.get("last_closed_at"),
                "returned_at": ch_state.get("last_activity"),
                "sender": msg.get("source"),
                "preview": msg.get("content", "")[:100],
            },
        )
