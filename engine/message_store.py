"""MessageStore — 消息历史 + edit 支持 (spec §3.1 schema)"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime

from zchat_protocol.message_types import Message, MessageVisibility


class MessageStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def save(self, message: Message) -> None:
        self._conn.execute(
            "INSERT INTO messages "
            "(id, conversation_id, source, content, visibility, timestamp, edit_of, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "content=excluded.content, visibility=excluded.visibility, "
            "timestamp=excluded.timestamp, edit_of=excluded.edit_of, metadata=excluded.metadata",
            (
                message.id,
                message.conversation_id,
                message.source,
                message.content,
                message.visibility.value,
                message.timestamp.isoformat(),
                message.edit_of,
                json.dumps(message.metadata, default=str, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def get(self, message_id: str) -> Message | None:
        row = self._conn.execute(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        return self._row_to_message(row) if row else None

    def edit(self, original_id: str, new_content: str) -> Message:
        original = self.get(original_id)
        if original is None:
            raise KeyError(f"Message not found: {original_id}")
        edited = Message(
            id=str(uuid.uuid4()),
            source=original.source,
            conversation_id=original.conversation_id,
            content=new_content,
            visibility=original.visibility,
            timestamp=datetime.now(),
            edit_of=original_id,
            metadata=dict(original.metadata),
        )
        self.save(edited)
        return edited

    def query_by_conversation(self, conversation_id: str) -> list[Message]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY timestamp ASC",
            (conversation_id,),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"],
            source=row["source"],
            conversation_id=row["conversation_id"],
            content=row["content"],
            visibility=MessageVisibility(row["visibility"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            edit_of=row["edit_of"],
            metadata=json.loads(row["metadata"]),
        )
