"""ParticipantRegistry unit tests — nick → role 映射"""

from __future__ import annotations

import pytest

from engine.participant_registry import ParticipantRegistry
from protocol.participant import ParticipantRole


@pytest.fixture
def registry():
    return ParticipantRegistry()


def test_register_agent(registry):
    p = registry.register_agent("alice-agent0")
    assert p.id == "alice-agent0"
    assert p.role == ParticipantRole.AGENT
    assert registry.identify("alice-agent0").role == ParticipantRole.AGENT


def test_register_operator(registry):
    p = registry.register_operator("xiaoli")
    assert p.role == ParticipantRole.OPERATOR
    assert registry.identify("xiaoli").id == "xiaoli"


def test_identify_unknown_returns_none(registry):
    assert registry.identify("ghost") is None


def test_bridge_mapping_returns_customer(registry):
    registry.register_bridge("feishu-bridge-1", "feishu")
    identified = registry.identify("feishu-bridge-1")
    assert identified is not None
    assert identified.role == ParticipantRole.CUSTOMER


def test_duplicate_agent_register_returns_existing(registry):
    a = registry.register_agent("alice-agent0")
    b = registry.register_agent("alice-agent0")
    assert a is b


def test_role_collision_detection(registry):
    registry.register_agent("dup")
    with pytest.raises(ValueError):
        registry.register_operator("dup")


def test_unregister(registry):
    registry.register_operator("xiaoli")
    registry.unregister("xiaoli")
    assert registry.identify("xiaoli") is None
