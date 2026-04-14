import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from protocol.message_types import MessageVisibility
from protocol.participant import Participant, ParticipantRole
from protocol.conversation import Conversation, ConversationState
from protocol.mode import ConversationMode
from protocol.gate import gate_message


def _conv(mode: ConversationMode) -> Conversation:
    c = Conversation(id="t", state=ConversationState.ACTIVE)
    c.mode = mode.value
    return c


# AUTO
def test_auto_agent_public():
    assert gate_message(_conv(ConversationMode.AUTO),
        Participant(id="a", role=ParticipantRole.AGENT),
        MessageVisibility.PUBLIC) == MessageVisibility.PUBLIC


# COPILOT — operator public 降级为 side
def test_copilot_operator_downgraded():
    assert gate_message(_conv(ConversationMode.COPILOT),
        Participant(id="op", role=ParticipantRole.OPERATOR),
        MessageVisibility.PUBLIC) == MessageVisibility.SIDE


def test_copilot_agent_passes():
    assert gate_message(_conv(ConversationMode.COPILOT),
        Participant(id="a", role=ParticipantRole.AGENT),
        MessageVisibility.PUBLIC) == MessageVisibility.PUBLIC


# TAKEOVER — agent public 降级为 side
def test_takeover_agent_downgraded():
    assert gate_message(_conv(ConversationMode.TAKEOVER),
        Participant(id="a", role=ParticipantRole.AGENT),
        MessageVisibility.PUBLIC) == MessageVisibility.SIDE


def test_takeover_operator_passes():
    assert gate_message(_conv(ConversationMode.TAKEOVER),
        Participant(id="op", role=ParticipantRole.OPERATOR),
        MessageVisibility.PUBLIC) == MessageVisibility.PUBLIC


# Side/System 不受影响
def test_side_stays_side():
    assert gate_message(_conv(ConversationMode.AUTO),
        Participant(id="a", role=ParticipantRole.AGENT),
        MessageVisibility.SIDE) == MessageVisibility.SIDE


def test_system_stays_system():
    assert gate_message(_conv(ConversationMode.TAKEOVER),
        Participant(id="a", role=ParticipantRole.AGENT),
        MessageVisibility.SYSTEM) == MessageVisibility.SYSTEM
