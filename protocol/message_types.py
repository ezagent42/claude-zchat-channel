"""Message 原语 — 消息与可见性 (spec §4 §5)"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MessageVisibility(Enum):
    PUBLIC = "public"   # 所有参与者可见（包括 customer）
    SIDE = "side"       # 只有 agent + operator + observer 可见
    SYSTEM = "system"   # 协议控制消息


@dataclass
class Message:
    id: str
    source: str  # 发送者 participant_id
    conversation_id: str
    content: str
    visibility: MessageVisibility
    timestamp: datetime = field(default_factory=datetime.now)
    edit_of: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
