"""ModeManager unit tests — 模式状态机 + 事件发出 (spec §3.2)"""

from __future__ import annotations

import pytest

from engine.event_bus import EventBus
from engine.mode_manager import ModeManager
from protocol.conversation import Conversation, ConversationState
from protocol.event import EventType
from protocol.mode import ConversationMode


@pytest.fixture
def bus(tmp_path):
    return EventBus(str(tmp_path / "e.db"))


@pytest.fixture
def mm(bus):
    return ModeManager(bus)


def test_transition(mm):
    conv = Conversation(id="c1", state=ConversationState.ACTIVE)
    mm.transition(conv, ConversationMode.COPILOT, "operator_joined", "xiaoli")
    assert conv.mode == "copilot"


def test_invalid_raises(mm):
    conv = Conversation(id="c1", state=ConversationState.ACTIVE)
    with pytest.raises(ValueError):
        mm.transition(conv, ConversationMode.AUTO, "noop", "test")


@pytest.mark.asyncio
async def test_transition_emits_event(bus, mm):
    received = []
    bus.subscribe(EventType.MODE_CHANGED, lambda e: received.append(e))
    conv = Conversation(id="c1", state=ConversationState.ACTIVE)
    await mm.atransition(conv, ConversationMode.COPILOT, "operator_joined", "xiaoli")
    assert len(received) == 1
    assert received[0].data["from"] == "auto"
    assert received[0].data["to"] == "copilot"
    assert received[0].data["trigger"] == "operator_joined"


def test_takeover_chain(mm):
    conv = Conversation(id="c1", state=ConversationState.ACTIVE)
    mm.transition(conv, ConversationMode.COPILOT, "operator_joined", "xiaoli")
    mm.transition(conv, ConversationMode.TAKEOVER, "/hijack", "xiaoli")
    assert conv.mode == "takeover"
    mm.transition(conv, ConversationMode.AUTO, "/release", "xiaoli")
    assert conv.mode == "auto"
