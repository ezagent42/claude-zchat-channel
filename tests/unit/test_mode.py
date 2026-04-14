import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from protocol.mode import ConversationMode, validate_transition, VALID_MODE_TRANSITIONS


def test_auto_to_copilot():
    t = validate_transition(ConversationMode.AUTO, ConversationMode.COPILOT,
                           trigger="operator_joined", triggered_by="xiaoli")
    assert t.to_mode == ConversationMode.COPILOT


def test_copilot_to_takeover():
    t = validate_transition(ConversationMode.COPILOT, ConversationMode.TAKEOVER,
                           trigger="/hijack", triggered_by="xiaoli")
    assert t.trigger == "/hijack"


def test_takeover_to_auto():
    validate_transition(ConversationMode.TAKEOVER, ConversationMode.AUTO,
                       trigger="/release", triggered_by="xiaoli")


def test_auto_to_auto_invalid():
    with pytest.raises(ValueError):
        validate_transition(ConversationMode.AUTO, ConversationMode.AUTO,
                           trigger="noop", triggered_by="test")


def test_all_valid_transitions_count():
    assert len(VALID_MODE_TRANSITIONS) == 6
