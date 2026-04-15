"""EventBus — 发布/订阅 + SQLite 持久化 (spec §3.5)

职责：
- 订阅者注册（按 EventType）
- 事件发布（持久化 → 通知订阅者）
- 历史事件查询（按 conversation_id / event_type / since）
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

from protocol.event import Event, EventType

log = logging.getLogger(__name__)


class EventBus:
    """事件总线：异步发布，所有事件落盘。"""

    def __init__(self, conn: sqlite3.Connection):
        self._subscribers: dict[EventType, list[Callable[[Event], Any]]] = defaultdict(
            list
        )
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def subscribe(
        self, event_type: EventType, callback: Callable[[Event], Any]
    ) -> None:
        """注册订阅者。回调可为 sync 或 async。"""
        self._subscribers[event_type].append(callback)

    async def publish(self, event: Event) -> None:
        """发布事件：先持久化，再通知订阅者。单个订阅者异常不影响其他订阅者。"""
        self._persist(event)
        for callback in list(self._subscribers.get(event.type, [])):
            try:
                result = callback(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # 记录但不中断
                log.error("Event subscriber error: %s", exc, exc_info=True)

    def _persist(self, event: Event) -> None:
        self._conn.execute(
            "INSERT INTO events (id, type, conversation_id, data, timestamp)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            " type=excluded.type, conversation_id=excluded.conversation_id,"
            " data=excluded.data, timestamp=excluded.timestamp",
            (
                event.id,
                event.type.value,
                event.conversation_id or None,
                json.dumps(event.data, default=str, ensure_ascii=False),
                event.timestamp.isoformat(),
            ),
        )
        self._conn.commit()

    def query(
        self,
        *,
        conversation_id: str | None = None,
        event_type: EventType | None = None,
        since: datetime | None = None,
    ) -> list[Event]:
        """查询历史事件，按 timestamp 升序。"""
        conditions: list[str] = []
        params: list[Any] = []
        if conversation_id is not None:
            conditions.append("conversation_id = ?")
            params.append(conversation_id)
        if event_type is not None:
            conditions.append("type = ?")
            params.append(event_type.value)
        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        sql = "SELECT id, type, conversation_id, data, timestamp FROM events"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY timestamp ASC"

        rows = self._conn.execute(sql, params).fetchall()
        events: list[Event] = []
        for row in rows:
            events.append(
                Event(
                    id=row["id"],
                    type=EventType(row["type"]),
                    conversation_id=row["conversation_id"] or "",
                    data=json.loads(row["data"]),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                )
            )
        return events

    def close(self) -> None:
        self._conn.close()
