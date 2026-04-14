"""TimerManager unit tests — asyncio 计时器 + 取消 + 事件 (spec §3.4)"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from engine.event_bus import EventBus
from engine.timer_manager import TimerManager
from protocol.event import EventType
from protocol.timer import TimerAction


@pytest.fixture
def setup(tmp_path):
    bus = EventBus(str(tmp_path / "e.db"))
    return TimerManager(bus), bus


@pytest.mark.asyncio
async def test_timer_fires(setup):
    tm, bus = setup
    fired = []
    bus.subscribe(EventType.TIMER_EXPIRED, lambda e: fired.append(e))
    tm.set_timer("c1", "test", timedelta(seconds=0.1), TimerAction(type="event"))
    await asyncio.sleep(0.3)
    assert len(fired) == 1
    assert fired[0].conversation_id == "c1"
    assert fired[0].data["name"] == "test"


@pytest.mark.asyncio
async def test_timer_cancel(setup):
    tm, bus = setup
    fired = []
    bus.subscribe(EventType.TIMER_EXPIRED, lambda e: fired.append(e))
    tm.set_timer("c1", "test", timedelta(seconds=0.5), TimerAction(type="event"))
    tm.cancel_timer("c1", "test")
    await asyncio.sleep(0.7)
    assert len(fired) == 0


@pytest.mark.asyncio
async def test_set_replaces_existing(setup):
    tm, bus = setup
    fired = []
    bus.subscribe(EventType.TIMER_EXPIRED, lambda e: fired.append(e))
    # 设置长 timer，再覆盖为短 timer
    tm.set_timer("c1", "t", timedelta(seconds=0.5), TimerAction(type="event"))
    tm.set_timer("c1", "t", timedelta(seconds=0.1), TimerAction(type="event"))
    await asyncio.sleep(0.3)
    assert len(fired) == 1  # 短的触发，长的被取消


@pytest.mark.asyncio
async def test_cancel_unknown_is_noop(setup):
    tm, _ = setup
    tm.cancel_timer("c_unknown", "missing")  # 不应抛异常


@pytest.mark.asyncio
async def test_timer_expired_clears_registry(setup):
    tm, _ = setup
    tm.set_timer("c1", "t", timedelta(seconds=0.05), TimerAction(type="event"))
    await asyncio.sleep(0.2)
    # 已过期，cancel 应为 no-op
    tm.cancel_timer("c1", "t")
