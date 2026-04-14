"""ConversationManager unit tests — CRUD + 状态机 + 并发上限 (spec §3.1)"""

from __future__ import annotations

import pytest

from engine.conversation_manager import ConcurrencyLimitExceeded, ConversationManager
from protocol.conversation import ConversationState
from protocol.participant import Participant, ParticipantRole


@pytest.fixture
def mgr(tmp_path):
    return ConversationManager(str(tmp_path / "conv.db"), max_operator_concurrent=2)


def test_create_and_get(mgr):
    conv = mgr.create("c1")
    assert conv.id == "c1"
    assert mgr.get("c1").id == "c1"


def test_get_unknown_returns_none(mgr):
    assert mgr.get("nope") is None


def test_create_idempotent(mgr):
    a = mgr.create("c1")
    b = mgr.create("c1")
    assert a.id == b.id == "c1"


def test_lifecycle(mgr):
    mgr.create("c1")
    mgr.activate("c1")
    assert mgr.get("c1").state == ConversationState.ACTIVE
    mgr.idle("c1")
    assert mgr.get("c1").state == ConversationState.IDLE
    mgr.reactivate("c1")
    assert mgr.get("c1").state == ConversationState.ACTIVE
    mgr.close("c1")
    assert mgr.get("c1").state == ConversationState.CLOSED


def test_operator_concurrency_limit(mgr):
    for i in range(3):
        mgr.create(f"c{i}")
        mgr.activate(f"c{i}")
    op = Participant(id="xiaoli", role=ParticipantRole.OPERATOR)
    mgr.add_participant("c0", op)
    mgr.add_participant("c1", op)
    with pytest.raises(ConcurrencyLimitExceeded):
        mgr.add_participant("c2", op)


def test_non_operator_not_limited(mgr):
    for i in range(5):
        mgr.create(f"c{i}")
        mgr.activate(f"c{i}")
    agent = Participant(id="agent0", role=ParticipantRole.AGENT)
    for i in range(5):
        mgr.add_participant(f"c{i}", agent)
    assert len(mgr.get("c0").participants) == 1


def test_remove_participant(mgr):
    mgr.create("c1")
    mgr.activate("c1")
    op = Participant(id="xiaoli", role=ParticipantRole.OPERATOR)
    mgr.add_participant("c1", op)
    mgr.remove_participant("c1", "xiaoli")
    assert not any(p.id == "xiaoli" for p in mgr.get("c1").participants)


def test_resolve(mgr):
    mgr.create("c1")
    mgr.activate("c1")
    mgr.resolve("c1", "resolved", "xiaoli")
    conv = mgr.get("c1")
    assert conv.state == ConversationState.CLOSED
    assert conv.resolution is not None
    assert conv.resolution.outcome == "resolved"
    assert conv.resolution.resolved_by == "xiaoli"


def test_set_csat_after_resolution(mgr):
    mgr.create("c1")
    mgr.activate("c1")
    mgr.resolve("c1", "resolved", "xiaoli")
    mgr.set_csat("c1", 5)
    assert mgr.get("c1").resolution.csat_score == 5


def test_list_active(mgr):
    mgr.create("c1")
    mgr.activate("c1")
    mgr.create("c2")
    mgr.activate("c2")
    mgr.create("c3")  # CREATED, not active
    actives = mgr.list_active()
    active_ids = {c.id for c in actives}
    assert active_ids == {"c1", "c2"}


def test_invalid_transition_raises(mgr):
    mgr.create("c1")
    # CREATED → IDLE 非法
    with pytest.raises(ValueError):
        mgr.idle("c1")


def test_persistence_survives_restart(tmp_path):
    db = str(tmp_path / "conv.db")
    m1 = ConversationManager(db)
    m1.create("c1", metadata={"channel": "feishu"})
    m1.activate("c1")
    op = Participant(id="xiaoli", role=ParticipantRole.OPERATOR)
    m1.add_participant("c1", op)
    m1.close_db()

    m2 = ConversationManager(db)
    conv = m2.get("c1")
    assert conv is not None
    assert conv.state == ConversationState.ACTIVE
    assert conv.metadata["channel"] == "feishu"
    assert any(p.id == "xiaoli" for p in conv.participants)


def test_closed_conversations_not_loaded_into_active_cache(tmp_path):
    db = str(tmp_path / "conv.db")
    m1 = ConversationManager(db)
    m1.create("c1")
    m1.activate("c1")
    m1.resolve("c1", "resolved", "xiaoli")
    m1.close_db()

    m2 = ConversationManager(db)
    # closed conversations 仍可通过 get 读出（按需从 db 懒加载），但不出现在 list_active
    assert all(c.id != "c1" for c in m2.list_active())
