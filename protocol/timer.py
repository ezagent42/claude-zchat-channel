"""Timer 原语 — 计时器与超时动作 (spec §6)"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class TimerAction:
    type: str  # "mode_change" | "system_message" | "callback"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Timer:
    conversation_id: str
    name: str  # 计时器名称（如 "takeover_wait"）
    duration: timedelta
    on_expire: TimerAction
    started_at: datetime = field(default_factory=datetime.now)
    cancelled: bool = False
