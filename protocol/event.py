"""Event 原语 — 事件类型与结构 (spec §7)"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class EventType(Enum):
    # Conversation 生命周期
    CONVERSATION_CREATED = "conversation.created"
    CONVERSATION_ACTIVATED = "conversation.activated"
    CONVERSATION_IDLED = "conversation.idled"
    CONVERSATION_REACTIVATED = "conversation.reactivated"
    CONVERSATION_CLOSED = "conversation.closed"
    CONVERSATION_RESOLVED = "conversation.resolved"
    CONVERSATION_CSAT_RECORDED = "conversation.csat_recorded"
    # Participant
    PARTICIPANT_JOINED = "participant.joined"
    PARTICIPANT_LEFT = "participant.left"
    # Mode
    MODE_CHANGED = "mode.changed"
    # Message
    MESSAGE_SENT = "message.sent"
    MESSAGE_EDITED = "message.edited"
    MESSAGE_GATED = "message.gated"
    MESSAGE_DELETED = "message.deleted"
    # Timer
    TIMER_SET = "timer.set"
    TIMER_EXPIRED = "timer.expired"
    TIMER_CANCELLED = "timer.cancelled"
    # SLA
    SLA_BREACH = "sla.breach"
    # Squad
    SQUAD_ASSIGNED = "squad.assigned"
    SQUAD_REASSIGNED = "squad.reassigned"


@dataclass
class Event:
    type: EventType
    conversation_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    id: str = field(default_factory=lambda: str(uuid4()))
