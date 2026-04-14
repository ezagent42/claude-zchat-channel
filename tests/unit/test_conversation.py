import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from protocol.conversation import (
    Conversation, ConversationState, ConversationResolution,
    create_conversation, transition_state, VALID_STATE_TRANSITIONS,
)


def test_create_conversation():
    conv = create_conversation("feishu_oc_abc", metadata={"source": "feishu"})
    assert conv.id == "feishu_oc_abc"
    assert conv.state == ConversationState.CREATED
    assert conv.metadata["source"] == "feishu"


def test_activate():
    conv = create_conversation("test_1")
    transition_state(conv, ConversationState.ACTIVE)
    assert conv.state == ConversationState.ACTIVE


def test_idle_and_reactivate():
    conv = create_conversation("test_2")
    transition_state(conv, ConversationState.ACTIVE)
    transition_state(conv, ConversationState.IDLE)
    assert conv.state == ConversationState.IDLE
    transition_state(conv, ConversationState.ACTIVE)
    assert conv.state == ConversationState.ACTIVE


def test_resolve_directly_from_active():
    conv = create_conversation("test_3")
    transition_state(conv, ConversationState.ACTIVE)
    transition_state(conv, ConversationState.CLOSED)
    assert conv.state == ConversationState.CLOSED


def test_invalid_transition_raises():
    conv = create_conversation("test_4")
    with pytest.raises(ValueError, match="Invalid state transition"):
        transition_state(conv, ConversationState.IDLE)


def test_resolution():
    conv = create_conversation("test_5")
    conv.resolution = ConversationResolution(
        outcome="resolved", resolved_by="xiaoli", csat_score=5
    )
    assert conv.resolution.csat_score == 5
