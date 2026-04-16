"""App plugin: SLA Timer 自动触发 (spec 06-gap-fixes §修复1)。

Hooks:
- on_conversation_created(conv_id, components) — 设 sla_onboard(默认30s，可配置)
- on_agent_public_message(conv_id, components) — 取消 sla_onboard
- on_placeholder_sent(conv_id, components) — 设 sla_slow_query(15s)；取消 sla_placeholder
- on_edit_sent(conv_id, components) — 取消 sla_slow_query

默认时长常量可被测试 patch（module-level 常量 → `patch("plugins.sla_app.SLA_*")`）。
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from zchat_protocol.timer import TimerAction

# SLA 默认时长（秒）。可通过同名环境变量覆盖；测试可 patch。
SLA_ONBOARD_DURATION_S: float = float(os.environ.get("SLA_ONBOARD_DURATION_S", "30"))
SLA_PLACEHOLDER_DURATION_S: float = float(os.environ.get("SLA_PLACEHOLDER_DURATION_S", "1.0"))
SLA_SLOW_QUERY_DURATION_S: float = float(os.environ.get("SLA_SLOW_QUERY_DURATION_S", "15.0"))


def _alert_action(duration_s: float) -> TimerAction:
    return TimerAction(type="alert", params={"duration_s": duration_s})


def on_conversation_created(conv_id: str, components: dict[str, Any]) -> None:
    tm = components.get("timer_manager")
    if tm is None or not conv_id:
        return
    tm.set_timer(
        conv_id,
        "sla_onboard",
        timedelta(seconds=SLA_ONBOARD_DURATION_S),
        _alert_action(SLA_ONBOARD_DURATION_S),
    )


def on_agent_public_message(conv_id: str, components: dict[str, Any]) -> None:
    tm = components.get("timer_manager")
    if tm is None or not conv_id:
        return
    tm.cancel_timer(conv_id, "sla_onboard")


def on_placeholder_sent(conv_id: str, components: dict[str, Any]) -> None:
    tm = components.get("timer_manager")
    if tm is None or not conv_id:
        return
    # 占位消息发出：placeholder 目标达成，取消 placeholder；启动 slow_query
    tm.cancel_timer(conv_id, "sla_placeholder")
    tm.set_timer(
        conv_id,
        "sla_slow_query",
        timedelta(seconds=SLA_SLOW_QUERY_DURATION_S),
        _alert_action(SLA_SLOW_QUERY_DURATION_S),
    )


def on_edit_sent(conv_id: str, components: dict[str, Any]) -> None:
    tm = components.get("timer_manager")
    if tm is None or not conv_id:
        return
    tm.cancel_timer(conv_id, "sla_slow_query")
