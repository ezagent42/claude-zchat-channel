"""Conversation Mode 原语 — 协作模式状态机 (spec §3)"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ConversationMode(Enum):
    AUTO = "auto"        # Agent 自主，无人工参与
    COPILOT = "copilot"  # Agent 主导，人工旁听+建议
    TAKEOVER = "takeover"  # 人工主导，Agent 副驾驶


# 合法模式转换表 — 6 条路径
VALID_MODE_TRANSITIONS: set[tuple[ConversationMode, ConversationMode]] = {
    (ConversationMode.AUTO, ConversationMode.COPILOT),    # operator JOIN
    (ConversationMode.AUTO, ConversationMode.TAKEOVER),   # /hijack 直接接管
    (ConversationMode.COPILOT, ConversationMode.TAKEOVER),  # /hijack 升级
    (ConversationMode.COPILOT, ConversationMode.AUTO),    # operator PART
    (ConversationMode.TAKEOVER, ConversationMode.AUTO),   # /release
    (ConversationMode.TAKEOVER, ConversationMode.COPILOT),  # /copilot 降级
}


@dataclass
class ModeTransition:
    from_mode: ConversationMode
    to_mode: ConversationMode
    trigger: str  # 触发命令或事件
    triggered_by: str  # participant_id
    timestamp: datetime = field(default_factory=datetime.now)


def validate_transition(
    from_mode: ConversationMode,
    to_mode: ConversationMode,
    *,
    trigger: str,
    triggered_by: str,
) -> ModeTransition:
    """验证模式转换合法性，返回 ModeTransition 记录。非法转换抛出 ValueError。"""
    if (from_mode, to_mode) not in VALID_MODE_TRANSITIONS:
        raise ValueError(
            f"Invalid mode transition: {from_mode.value} → {to_mode.value}"
        )
    return ModeTransition(
        from_mode=from_mode,
        to_mode=to_mode,
        trigger=trigger,
        triggered_by=triggered_by,
    )
