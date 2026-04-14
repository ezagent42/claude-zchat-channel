"""ModeManager — 模式切换 + 合法性验证 + 事件发出 (spec §3.2)"""

from __future__ import annotations

from engine.event_bus import EventBus
from protocol.conversation import Conversation
from protocol.event import Event, EventType
from protocol.mode import (
    ConversationMode,
    ModeTransition,
    validate_transition,
)


class ModeManager:
    def __init__(self, event_bus: EventBus):
        self._bus = event_bus

    def transition(
        self,
        conversation: Conversation,
        new_mode: ConversationMode,
        trigger: str,
        triggered_by: str,
    ) -> ModeTransition:
        """同步：验证 + 更新 conversation.mode。不发 event。"""
        from_mode = ConversationMode(conversation.mode)
        transition = validate_transition(
            from_mode,
            new_mode,
            trigger=trigger,
            triggered_by=triggered_by,
        )
        conversation.mode = new_mode.value
        return transition

    async def atransition(
        self,
        conversation: Conversation,
        new_mode: ConversationMode,
        trigger: str,
        triggered_by: str,
    ) -> ModeTransition:
        """异步：同步 transition + 发出 mode.changed 事件。"""
        transition = self.transition(conversation, new_mode, trigger, triggered_by)
        await self._bus.publish(
            Event(
                type=EventType.MODE_CHANGED,
                conversation_id=conversation.id,
                data={
                    "from": transition.from_mode.value,
                    "to": transition.to_mode.value,
                    "trigger": trigger,
                    "triggered_by": triggered_by,
                },
            )
        )
        return transition
