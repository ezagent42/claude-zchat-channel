"""EventBus unit tests — 发布/订阅 + SQLite 持久化 (spec §3.5)"""

from __future__ import annotations

import pytest

from engine.event_bus import EventBus
from zchat_protocol.event import Event, EventType


def _seed_conversations(conn, *conv_ids):
    """预插入 conversation 行以满足 FK 约束。"""
    for cid in conv_ids:
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, state, mode, created_at, updated_at) "
            "VALUES (?, 'active', 'auto', '2026-01-01', '2026-01-01')",
            (cid,),
        )
    conn.commit()


@pytest.fixture
def bus(tmp_path):
    from engine.db import init_db

    conn = init_db(str(tmp_path / "events.db"))
    _seed_conversations(conn, "c1")
    return EventBus(conn)


@pytest.mark.asyncio
async def test_publish_and_subscribe(bus):
    received = []
    bus.subscribe(EventType.CONVERSATION_CREATED, lambda e: received.append(e))
    await bus.publish(Event(type=EventType.CONVERSATION_CREATED, conversation_id="c1"))
    assert len(received) == 1
    assert received[0].conversation_id == "c1"


@pytest.mark.asyncio
async def test_persisted_to_sqlite(bus):
    await bus.publish(
        Event(
            type=EventType.MODE_CHANGED,
            conversation_id="c1",
            data={"from": "auto", "to": "copilot"},
        )
    )
    results = bus.query(conversation_id="c1")
    assert len(results) == 1
    assert results[0].type == EventType.MODE_CHANGED
    assert results[0].data["to"] == "copilot"


@pytest.mark.asyncio
async def test_query_by_type(bus):
    await bus.publish(Event(type=EventType.MODE_CHANGED, conversation_id="c1"))
    await bus.publish(Event(type=EventType.MESSAGE_SENT, conversation_id="c1"))
    results = bus.query(event_type=EventType.MODE_CHANGED)
    assert len(results) == 1
    assert results[0].type == EventType.MODE_CHANGED


@pytest.mark.asyncio
async def test_async_subscriber(bus):
    received = []

    async def handler(e):
        received.append(e)

    bus.subscribe(EventType.CONVERSATION_CREATED, handler)
    await bus.publish(Event(type=EventType.CONVERSATION_CREATED, conversation_id="c1"))
    assert len(received) == 1


@pytest.mark.asyncio
async def test_subscriber_exception_is_isolated(bus):
    received = []

    def bad(e):
        raise RuntimeError("boom")

    bus.subscribe(EventType.CONVERSATION_CREATED, bad)
    bus.subscribe(EventType.CONVERSATION_CREATED, lambda e: received.append(e))
    # 异常不应阻塞后续订阅者
    await bus.publish(Event(type=EventType.CONVERSATION_CREATED, conversation_id="c1"))
    assert len(received) == 1


@pytest.mark.asyncio
async def test_query_persistence_across_instances(tmp_path):
    from engine.db import init_db

    db = str(tmp_path / "events.db")
    conn1 = init_db(db)
    _seed_conversations(conn1, "c1")
    bus1 = EventBus(conn1)
    await bus1.publish(Event(type=EventType.CONVERSATION_CLOSED, conversation_id="c1"))
    conn1.close()
    conn2 = init_db(db)
    bus2 = EventBus(conn2)
    results = bus2.query(conversation_id="c1")
    assert len(results) == 1
    assert results[0].type == EventType.CONVERSATION_CLOSED
