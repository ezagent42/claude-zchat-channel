"""TimerManager — asyncio 计时器管理 (spec §3.4)

每个 (conversation_id, timer_name) 对应一个 asyncio.Task。
- set_timer: 若 key 已存在，先 cancel 旧任务再创建新任务
- cancel_timer: 取消并移除
- 超时后发出 TIMER_EXPIRED 事件
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from engine.event_bus import EventBus
from protocol.event import Event, EventType
from protocol.timer import Timer, TimerAction


class TimerManager:
    def __init__(self, event_bus: EventBus):
        self._bus = event_bus
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    def set_timer(
        self,
        conv_id: str,
        name: str,
        duration: timedelta,
        on_expire: TimerAction,
    ) -> Timer:
        key = (conv_id, name)
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            existing.cancel()

        timer = Timer(
            conversation_id=conv_id,
            name=name,
            duration=duration,
            on_expire=on_expire,
            started_at=datetime.now(),
        )
        task = asyncio.create_task(self._wait_and_fire(timer))
        self._tasks[key] = task
        return timer

    def cancel_timer(self, conv_id: str, name: str) -> None:
        key = (conv_id, name)
        task = self._tasks.pop(key, None)
        if task is None:
            return
        if not task.done():
            task.cancel()

    async def _wait_and_fire(self, timer: Timer) -> None:
        try:
            await asyncio.sleep(timer.duration.total_seconds())
        except asyncio.CancelledError:
            timer.cancelled = True
            return
        # 只有未被新 set 覆盖的 timer 才应该 fire
        key = (timer.conversation_id, timer.name)
        if self._tasks.get(key) is not asyncio.current_task():
            return
        self._tasks.pop(key, None)
        await self._bus.publish(
            Event(
                type=EventType.TIMER_EXPIRED,
                conversation_id=timer.conversation_id,
                data={
                    "name": timer.name,
                    "action_type": timer.on_expire.type,
                    "action_params": timer.on_expire.params,
                },
            )
        )
