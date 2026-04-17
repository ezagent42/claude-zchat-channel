import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from zchat_protocol.event import Event, EventType


def test_event_creation():
    e = Event(type=EventType.CONVERSATION_CREATED, conversation_id="t1", data={})
    assert e.id  # auto UUID


def test_event_types_complete():
    for name in ["CONVERSATION_CREATED", "MODE_CHANGED", "MESSAGE_SENT",
                 "MESSAGE_GATED", "TIMER_EXPIRED",
                 "CONVERSATION_RESOLVED"]:
        assert hasattr(EventType, name)
