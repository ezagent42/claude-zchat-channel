"""EventBus unit tests — 发布/订阅 + SQLite 持久化 (spec §3.5)"""

from __future__ import annotations

import pytest

from engine.event_bus import EventBus
from protocol.event import Event, EventType


@pytest.fixture
def bus(tmp_path):
    return EventBus(str(tmp_path / "events.db"))


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
    db = str(tmp_path / "events.db")
    bus1 = EventBus(db)
    await bus1.publish(Event(type=EventType.CONVERSATION_CLOSED, conversation_id="c1"))
    bus1.close()
    bus2 = EventBus(db)
    results = bus2.query(conversation_id="c1")
    assert len(results) == 1
    assert results[0].type == EventType.CONVERSATION_CLOSED
