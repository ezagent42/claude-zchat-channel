"""ConversationManager — 对话 CRUD + 状态机 + SQLite 持久化 (spec §3.1)

运行时模型：
- 内存 dict 作为 active/idle 的快速路由表
- SQLite 作为权威持久层（closed 也保留）
- 所有写操作同时更新内存和 db
- 启动时从 db 加载 state != closed 的对话
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from protocol.conversation import (
    Conversation,
    ConversationResolution,
    ConversationState,
    transition_state,
)
from protocol.participant import Participant, ParticipantRole


class ConcurrencyLimitExceeded(Exception):
    """Operator 同时处理的 conversation 超过上限。"""


class ConversationManager:
    def __init__(self, db_path: str, max_operator_concurrent: int = 5):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conversations: dict[str, Conversation] = {}
        self.max_operator_concurrent = max_operator_concurrent
        self._init_db()
        self._load_active()

    # ---------- schema ----------

    def _init_db(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'created',
                mode TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS participants (
                conversation_id TEXT NOT NULL,
                participant_id TEXT NOT NULL,
                role TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (conversation_id, participant_id)
            );
            CREATE TABLE IF NOT EXISTS resolutions (
                conversation_id TEXT PRIMARY KEY,
                outcome TEXT NOT NULL,
                resolved_by TEXT NOT NULL,
                csat_score INTEGER,
                timestamp TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def _load_active(self) -> None:
        rows = self._conn.execute(
            "SELECT id FROM conversations WHERE state != ?",
            (ConversationState.CLOSED.value,),
        ).fetchall()
        for row in rows:
            conv = self._load_from_db(row["id"])
            if conv is not None:
                self._conversations[conv.id] = conv

    def _load_from_db(self, conv_id: str) -> Conversation | None:
        row = self._conn.execute(
            "SELECT id, state, mode, created_at, updated_at, metadata"
            " FROM conversations WHERE id = ?",
            (conv_id,),
        ).fetchone()
        if row is None:
            return None
        conv = Conversation(
            id=row["id"],
            state=ConversationState(row["state"]),
            mode=row["mode"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["metadata"]),
        )
        part_rows = self._conn.execute(
            "SELECT participant_id, role, joined_at, metadata FROM participants"
            " WHERE conversation_id = ?",
            (conv_id,),
        ).fetchall()
        for p in part_rows:
            conv.participants.append(
                Participant(
                    id=p["participant_id"],
                    role=ParticipantRole(p["role"]),
                    joined_at=datetime.fromisoformat(p["joined_at"]),
                    metadata=json.loads(p["metadata"]),
                )
            )
        res_row = self._conn.execute(
            "SELECT outcome, resolved_by, csat_score, timestamp FROM resolutions"
            " WHERE conversation_id = ?",
            (conv_id,),
        ).fetchone()
        if res_row is not None:
            conv.resolution = ConversationResolution(
                outcome=res_row["outcome"],
                resolved_by=res_row["resolved_by"],
                csat_score=res_row["csat_score"],
                timestamp=datetime.fromisoformat(res_row["timestamp"]),
            )
        return conv

    # ---------- CRUD ----------

    def create(
        self, conversation_id: str, metadata: dict[str, Any] | None = None
    ) -> Conversation:
        existing = self.get(conversation_id)
        if existing is not None:
            return existing
        conv = Conversation(id=conversation_id, metadata=metadata or {})
        self._conversations[conversation_id] = conv
        self._upsert_conversation(conv)
        return conv

    def get(self, conversation_id: str) -> Conversation | None:
        if conversation_id in self._conversations:
            return self._conversations[conversation_id]
        # 懒加载 closed 的 conversation
        conv = self._load_from_db(conversation_id)
        if conv is not None:
            self._conversations[conversation_id] = conv
        return conv

    def list_active(self) -> list[Conversation]:
        return [
            c
            for c in self._conversations.values()
            if c.state == ConversationState.ACTIVE
        ]

    # ---------- 状态转换 ----------

    def activate(self, conversation_id: str) -> None:
        self._transition(conversation_id, ConversationState.ACTIVE)

    def idle(self, conversation_id: str) -> None:
        self._transition(conversation_id, ConversationState.IDLE)

    def reactivate(self, conversation_id: str) -> None:
        self._transition(conversation_id, ConversationState.ACTIVE)

    def close(self, conversation_id: str) -> None:
        self._transition(conversation_id, ConversationState.CLOSED)

    def _transition(self, conv_id: str, target: ConversationState) -> None:
        conv = self._require(conv_id)
        transition_state(conv, target)
        self._upsert_conversation(conv)

    # ---------- participants ----------

    def add_participant(
        self, conversation_id: str, participant: Participant
    ) -> None:
        conv = self._require(conversation_id)
        if participant.role == ParticipantRole.OPERATOR:
            current = self._count_operator_active(participant.id)
            already_in_this = any(
                p.id == participant.id for p in conv.participants
            )
            if not already_in_this and current >= self.max_operator_concurrent:
                raise ConcurrencyLimitExceeded(
                    f"Operator {participant.id} 已达并发上限 "
                    f"({current}/{self.max_operator_concurrent})"
                )
        # 去重
        if not any(p.id == participant.id for p in conv.participants):
            conv.participants.append(participant)
        self._conn.execute(
            "INSERT OR REPLACE INTO participants "
            "(conversation_id, participant_id, role, joined_at, metadata) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                conversation_id,
                participant.id,
                participant.role.value,
                participant.joined_at.isoformat(),
                json.dumps(participant.metadata, default=str, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def remove_participant(
        self, conversation_id: str, participant_id: str
    ) -> None:
        conv = self._require(conversation_id)
        conv.participants = [p for p in conv.participants if p.id != participant_id]
        self._conn.execute(
            "DELETE FROM participants WHERE conversation_id = ? AND participant_id = ?",
            (conversation_id, participant_id),
        )
        self._conn.commit()

    def _count_operator_active(self, operator_id: str) -> int:
        count = 0
        for conv in self._conversations.values():
            if conv.state != ConversationState.ACTIVE:
                continue
            if any(p.id == operator_id for p in conv.participants):
                count += 1
        return count

    # ---------- resolution ----------

    def resolve(
        self, conversation_id: str, outcome: str, resolved_by: str
    ) -> None:
        conv = self._require(conversation_id)
        conv.resolution = ConversationResolution(
            outcome=outcome, resolved_by=resolved_by
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO resolutions "
            "(conversation_id, outcome, resolved_by, csat_score, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                conversation_id,
                outcome,
                resolved_by,
                conv.resolution.csat_score,
                conv.resolution.timestamp.isoformat(),
            ),
        )
        self._conn.commit()
        self.close(conversation_id)

    def set_csat(self, conversation_id: str, score: int) -> None:
        conv = self._require(conversation_id)
        if conv.resolution is None:
            raise ValueError(f"Conversation {conversation_id} has no resolution")
        if not 1 <= score <= 5:
            raise ValueError(f"CSAT score must be 1..5, got {score}")
        conv.resolution.csat_score = score
        self._conn.execute(
            "UPDATE resolutions SET csat_score = ? WHERE conversation_id = ?",
            (score, conversation_id),
        )
        self._conn.commit()

    # ---------- helpers ----------

    def _require(self, conv_id: str) -> Conversation:
        conv = self.get(conv_id)
        if conv is None:
            raise KeyError(f"Conversation not found: {conv_id}")
        return conv

    def _upsert_conversation(self, conv: Conversation) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO conversations "
            "(id, state, mode, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                conv.id,
                conv.state.value,
                conv.mode,
                conv.created_at.isoformat(),
                conv.updated_at.isoformat(),
                json.dumps(conv.metadata, default=str, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def close_db(self) -> None:
        self._conn.close()
