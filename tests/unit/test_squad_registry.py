"""SquadRegistry unit tests — Agent-Operator 分队管理"""

from __future__ import annotations

import pytest

from engine.squad_registry import SquadRegistry


@pytest.fixture
def squad():
    return SquadRegistry()


def test_assign_and_get_squad(squad):
    squad.assign("agent_fast", "xiaoli")
    assert squad.get_operator("agent_fast") == "xiaoli"
    assert squad.get_squad("xiaoli") == ["agent_fast"]


def test_operator_with_multiple_agents(squad):
    squad.assign("agent_fast", "xiaoli")
    squad.assign("agent_deep", "xiaoli")
    assert set(squad.get_squad("xiaoli")) == {"agent_fast", "agent_deep"}


def test_reassign(squad):
    squad.assign("agent_fast", "xiaoli")
    squad.reassign("agent_fast", "xiaowang")
    assert squad.get_operator("agent_fast") == "xiaowang"
    assert "agent_fast" not in squad.get_squad("xiaoli")
    assert "agent_fast" in squad.get_squad("xiaowang")


def test_get_operator_unknown(squad):
    assert squad.get_operator("ghost") is None


def test_get_squad_unknown_returns_empty(squad):
    assert squad.get_squad("ghost") == []


def test_unassign(squad):
    squad.assign("agent_fast", "xiaoli")
    squad.unassign("agent_fast")
    assert squad.get_operator("agent_fast") is None
    assert squad.get_squad("xiaoli") == []


def test_assign_idempotent(squad):
    squad.assign("agent_fast", "xiaoli")
    squad.assign("agent_fast", "xiaoli")
    assert squad.get_squad("xiaoli") == ["agent_fast"]
