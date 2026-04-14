"""Conversation 原语 — 对话生命周期状态机 (spec §1)"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ConversationState(Enum):
    CREATED = "created"
    ACTIVE = "active"
    IDLE = "idle"
    CLOSED = "closed"


# 合法状态转换表
VALID_STATE_TRANSITIONS: dict[ConversationState, set[ConversationState]] = {
    ConversationState.CREATED: {ConversationState.ACTIVE},
    ConversationState.ACTIVE: {ConversationState.IDLE, ConversationState.CLOSED},
    ConversationState.IDLE: {ConversationState.ACTIVE, ConversationState.CLOSED},
    ConversationState.CLOSED: set(),
}


@dataclass
class ConversationResolution:
    outcome: str  # "resolved" | "abandoned" | "escalated"
    resolved_by: str  # participant_id
    csat_score: int | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Conversation:
    id: str
    state: ConversationState = ConversationState.CREATED
    mode: str = "auto"
    participants: list[Any] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    resolution: ConversationResolution | None = None


def create_conversation(
    conversation_id: str, *, metadata: dict[str, Any] | None = None
) -> Conversation:
    """创建新对话，初始状态为 CREATED。"""
    return Conversation(id=conversation_id, metadata=metadata or {})


def transition_state(conv: Conversation, target: ConversationState) -> None:
    """执行状态转换，非法转换抛出 ValueError。"""
    valid = VALID_STATE_TRANSITIONS.get(conv.state, set())
    if target not in valid:
        raise ValueError(
            f"Invalid state transition: {conv.state.value} → {target.value}"
        )
    conv.state = target
    conv.updated_at = datetime.now()
