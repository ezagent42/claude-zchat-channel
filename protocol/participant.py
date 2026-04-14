"""Participant 原语 — 参与者与角色 (spec §2)"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ParticipantRole(Enum):
    CUSTOMER = "customer"
    AGENT = "agent"
    OPERATOR = "operator"
    OBSERVER = "observer"


@dataclass
class Participant:
    id: str
    role: ParticipantRole
    joined_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
