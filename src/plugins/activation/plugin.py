"""activation plugin — channel 活跃度检测 + 客户回访事件。

职责：
  - 订阅所有 message：更新 channel.last_activity
  - 订阅 channel_resolved：标记 channel.last_closed_at + is_dormant=True
  - 订阅 on_ws_message：检测客户在 dormant channel 发言 → emit customer_returned

持久化到 activation-state.json。

策略选择留给 bridge / admin-agent 决定（本 plugin 只检测并 emit）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from channel_server.plugin import BasePlugin

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActivationPlugin(BasePlugin):
    """channel 活跃度跟踪 + customer_returned 事件。"""

    name = "activation"

    def __init__(
        self,
        config: dict,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
    ) -> None:
        """V7 config-driven signature.

        config:
          - data_dir (optional): plugin 状态根目录。
        """
        data_dir = Path(config.get("data_dir") or ".")
        self._path = data_dir / "state.json"
        self._emit_event = emit_event
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"channels": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            data.setdefault("channels", {})
            return data
        except Exception as e:
            log.warning("activation state load failed: %s", e)
            return {"channels": {}}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as e:
            log.exception("activation save failed: %s", e)

    def _ch(self, channel: str) -> dict[str, Any]:
        channels = self._state.setdefault("channels", {})
        if channel not in channels:
            channels[channel] = {
                "last_activity": None,
                "last_closed_at": None,
                "is_dormant": False,
            }
        return channels[channel]

    def handles_commands(self) -> list[str]:
        return []

    async def on_ws_message(self, msg: dict) -> None:
        channel = msg.get("channel") or ""
        if not channel:
            return
        ch = self._ch(channel)
        was_dormant = ch.get("is_dormant", False)
        ch["last_activity"] = _now_iso()
        if was_dormant:
            # 客户回访了一个 dormant channel
            ch["is_dormant"] = False
            self._save()
            try:
                await self._emit_event(
                    "customer_returned",
                    channel,
                    {
                        "sender": msg.get("source"),
                        "preview": str(msg.get("content", ""))[:100],
                        "last_closed_at": ch.get("last_closed_at"),
                    },
                )
            except Exception:
                log.exception("[activation] emit customer_returned failed")
        else:
            self._save()

    async def on_ws_event(self, event: dict) -> None:
        name = event.get("event") or ""
        channel = event.get("channel") or ""
        if not channel:
            return
        if name == "channel_resolved":
            ch = self._ch(channel)
            ch["last_closed_at"] = _now_iso()
            ch["is_dormant"] = True
            self._save()

    def query(self, key: str, args: dict | None = None) -> Any:
        args = args or {}
        channel = args.get("channel", "")
        if key == "last_activity":
            return self._ch(channel).get("last_activity")
        if key == "last_closed":
            return self._ch(channel).get("last_closed_at")
        if key == "is_dormant":
            return self._ch(channel).get("is_dormant", False)
        return None
