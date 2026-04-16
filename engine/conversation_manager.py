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

from zchat_protocol.conversation import (
    Conversation,
    ConversationResolution,
    ConversationState,
    transition_state,
)
from zchat_protocol.participant import Participant, ParticipantRole


class ConcurrencyLimitExceeded(Exception):
    """Operator 同时处理的 conversation 超过上限。"""


class ConversationManager:
    def __init__(self, conn: sqlite3.Connection, max_operator_concurrent: int = 5):
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._conversations: dict[str, Conversation] = {}
        self.max_operator_concurrent = max_operator_concurrent
        self._load_active()

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

    def find_conversation_by_agent(self, agent_nick: str) -> str | None:
        """查找 agent 所参与的 conversation id（用于 PRIVMSG 路由）。"""
        for cid, conv in self._conversations.items():
            for p in conv.participants:
                if p.id == agent_nick and p.role == ParticipantRole.AGENT:
                    return cid
        return None

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
            "INSERT INTO participants "
            "(conversation_id, participant_id, role, joined_at, metadata) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(conversation_id, participant_id) DO UPDATE SET "
            "role=excluded.role, joined_at=excluded.joined_at, metadata=excluded.metadata",
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
            "INSERT INTO resolutions "
            "(conversation_id, outcome, resolved_by, csat_score, timestamp) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET "
            "outcome=excluded.outcome, resolved_by=excluded.resolved_by, "
            "csat_score=excluded.csat_score, timestamp=excluded.timestamp",
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
            "INSERT INTO conversations "
            "(id, state, mode, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "state=excluded.state, mode=excluded.mode, "
            "updated_at=excluded.updated_at, metadata=excluded.metadata",
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
