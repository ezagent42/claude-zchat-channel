import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from zchat_protocol.participant import Participant, ParticipantRole


def test_create_customer():
    p = Participant(id="david", role=ParticipantRole.CUSTOMER)
    assert p.role == ParticipantRole.CUSTOMER


def test_create_agent():
    p = Participant(id="fast-agent", role=ParticipantRole.AGENT)
    assert p.role == ParticipantRole.AGENT


def test_roles_are_distinct():
    assert len(set(ParticipantRole)) == 4
