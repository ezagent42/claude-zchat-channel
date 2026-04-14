"""Message Gate — 消息门控纯函数 (spec §5)

Gate 是 mechanism 级保证：根据 conversation.mode + sender.role 决定最终 visibility。
Agent 在 takeover 模式下无法绕过 gate 直接向客户发消息。
"""

from __future__ import annotations

from protocol.conversation import Conversation
from protocol.message_types import MessageVisibility
from protocol.mode import ConversationMode
from protocol.participant import Participant, ParticipantRole


def gate_message(
    conversation: Conversation,
    sender: Participant,
    requested_visibility: MessageVisibility,
) -> MessageVisibility:
    """决定消息的最终可见性。纯函数，无副作用。"""
    # system / side 消息不受 gate 影响（不可逆降级原则）
    if requested_visibility == MessageVisibility.SYSTEM:
        return MessageVisibility.SYSTEM
    if requested_visibility == MessageVisibility.SIDE:
        return MessageVisibility.SIDE

    # 以下处理 requested_visibility == PUBLIC
    mode = ConversationMode(conversation.mode)
    role = sender.role

    if mode == ConversationMode.AUTO:
        return MessageVisibility.PUBLIC

    if mode == ConversationMode.COPILOT:
        if role == ParticipantRole.OPERATOR:
            return MessageVisibility.SIDE  # operator 消息降级
        return MessageVisibility.PUBLIC

    if mode == ConversationMode.TAKEOVER:
        if role == ParticipantRole.AGENT:
            return MessageVisibility.SIDE  # agent 消息降级
        return MessageVisibility.PUBLIC

    return requested_visibility
