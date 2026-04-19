"""audit plugin — 运营数据统计（仪表盘 + /status + /review 的数据源）。

持久化到 audit.json，记录：
  - 每个 channel 的状态（active / takeover / resolved）
  - takeover 事件（次数 + 时间戳 + 释放者）
  - 升级转结案率（takeover → resolve 配对）
  - CSAT 分数（由 csat plugin 调 record_csat 写入）
  - 首次回复时间、会话时长

该 plugin 只订阅事件，不处理命令。数据读取通过 query 接口（router 或 CLI 调用）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from channel_server.plugin import BasePlugin

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditPlugin(BasePlugin):
    """运行时统计 + JSON 持久化。"""

    name = "audit"

    def __init__(self, persist_path: str | Path) -> None:
        self._path = Path(persist_path)
        self._state = self._load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"channels": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            data.setdefault("channels", {})
            return data
        except Exception as e:
            log.warning("audit state load failed: %s", e)
            return {"channels": {}}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as e:
            log.exception("audit state save failed: %s", e)

    def _ch(self, channel: str) -> dict[str, Any]:
        """获取或初始化某 channel 的统计条目。"""
        channels = self._state.setdefault("channels", {})
        if channel not in channels:
            channels[channel] = {
                "state": "active",
                "created_at": _now_iso(),
                "first_message_at": None,
                "first_reply_at": None,
                "takeovers": [],
                "resolved_at": None,
                "message_count": 0,
                "csat_score": None,
            }
        return channels[channel]

    # ------------------------------------------------------------------
    # 事件订阅
    # ------------------------------------------------------------------

    def handles_commands(self) -> list[str]:
        return []

    async def on_ws_message(self, msg: dict) -> None:
        channel = msg.get("channel") or ""
        if not channel:
            return
        ch = self._ch(channel)
        ch["message_count"] = ch.get("message_count", 0) + 1
        if ch.get("first_message_at") is None:
            ch["first_message_at"] = _now_iso()
        # 如果是 agent 回复（source 匹配 agent nick 约定），记录 first_reply
        source = msg.get("source") or ""
        if ch.get("first_reply_at") is None and self._looks_like_agent(source):
            ch["first_reply_at"] = _now_iso()
        self._save()

    async def on_ws_event(self, event: dict) -> None:
        name = event.get("event") or ""
        channel = event.get("channel") or ""
        data = event.get("data") or {}
        if not channel:
            return

        ch = self._ch(channel)

        if name == "mode_changed":
            to = data.get("to")
            if to == "takeover":
                ch["state"] = "takeover"
                ch.setdefault("takeovers", []).append({
                    "at": _now_iso(),
                    "triggered_by": data.get("triggered_by", "unknown"),
                    "released_at": None,
                    "released_by": None,
                })
            elif to == "copilot":
                ch["state"] = "active"
                # 找最后一个未释放的 takeover
                for tk in reversed(ch.get("takeovers", [])):
                    if tk.get("released_at") is None:
                        tk["released_at"] = _now_iso()
                        tk["released_by"] = data.get("triggered_by", "unknown")
                        break
            self._save()

        elif name == "channel_resolved":
            ch["state"] = "resolved"
            ch["resolved_at"] = _now_iso()
            self._save()

    # ------------------------------------------------------------------
    # 外部写入接口（csat plugin 用）
    # ------------------------------------------------------------------

    def record_csat(self, channel: str, score: int) -> None:
        """被 csat plugin 调用，记录客户评分。"""
        ch = self._ch(channel)
        ch["csat_score"] = score
        self._save()

    # ------------------------------------------------------------------
    # 查询接口（router / CLI / admin-agent 使用）
    # ------------------------------------------------------------------

    def query(self, key: str, args: dict | None = None) -> Any:
        args = args or {}
        if key == "status":
            channel = args.get("channel")
            if channel:
                return self._state.get("channels", {}).get(channel)
            # 全局：活跃 channel 列表 + 聚合
            return {
                "channels": self._state.get("channels", {}),
                "aggregates": self._compute_aggregates(),
            }
        if key == "report":
            return {"aggregates": self._compute_aggregates()}
        return None

    def _compute_aggregates(self) -> dict[str, Any]:
        channels = self._state.get("channels", {}).values()
        total_takeovers = 0
        total_resolved = 0
        takeover_then_resolve = 0
        csat_scores = []
        for ch in channels:
            takeovers = ch.get("takeovers") or []
            total_takeovers += len(takeovers)
            if ch.get("state") == "resolved":
                total_resolved += 1
                if takeovers:
                    takeover_then_resolve += 1
            if ch.get("csat_score") is not None:
                csat_scores.append(ch["csat_score"])

        escalation_resolve_rate = (
            takeover_then_resolve / total_takeovers if total_takeovers else 0.0
        )
        csat_mean = sum(csat_scores) / len(csat_scores) if csat_scores else None
        return {
            "total_channels": len(self._state.get("channels", {})),
            "total_takeovers": total_takeovers,
            "total_resolved": total_resolved,
            "escalation_resolve_rate": round(escalation_resolve_rate, 3),
            "csat_mean": round(csat_mean, 2) if csat_mean is not None else None,
        }

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_agent(source: str) -> bool:
        """简单判断 source 是否像 agent nick（包含 '-' 且以字母数字结尾）。"""
        if not source or "-" not in source:
            return False
        # 约定 agent nick 格式是 username-name，包含 -
        # 不 match cs-bot / internal / customer 等系统源
        return source not in ("cs-bot", "internal", "customer", "card_action", "operator", "admin")
