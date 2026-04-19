"""AuditPlugin 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.audit.plugin import AuditPlugin


@pytest.fixture
def audit(tmp_path) -> AuditPlugin:
    return AuditPlugin(persist_path=tmp_path / "audit.json")


def test_handles_no_commands(audit):
    assert audit.handles_commands() == []


@pytest.mark.asyncio
async def test_on_message_increments_count(audit):
    await audit.on_ws_message({"channel": "c1", "source": "customer", "content": "hi"})
    await audit.on_ws_message({"channel": "c1", "source": "customer", "content": "again"})
    status = audit.query("status", {"channel": "c1"})
    assert status["message_count"] == 2
    assert status["first_message_at"] is not None


@pytest.mark.asyncio
async def test_on_message_records_first_reply_only_for_agents(audit):
    # 客户发的不算 first_reply
    await audit.on_ws_message({"channel": "c1", "source": "customer", "content": "hi"})
    status1 = audit.query("status", {"channel": "c1"})
    assert status1["first_reply_at"] is None

    # agent 发的算（nick 带 '-'）
    await audit.on_ws_message({"channel": "c1", "source": "alice-fast-001", "content": "hello"})
    status2 = audit.query("status", {"channel": "c1"})
    assert status2["first_reply_at"] is not None


@pytest.mark.asyncio
async def test_mode_changed_to_takeover_records(audit):
    await audit.on_ws_event({
        "event": "mode_changed",
        "channel": "c1",
        "data": {"to": "takeover", "triggered_by": "operator"},
    })
    status = audit.query("status", {"channel": "c1"})
    assert status["state"] == "takeover"
    assert len(status["takeovers"]) == 1
    assert status["takeovers"][0]["triggered_by"] == "operator"
    assert status["takeovers"][0]["released_at"] is None


@pytest.mark.asyncio
async def test_mode_changed_to_copilot_closes_takeover(audit):
    await audit.on_ws_event({
        "event": "mode_changed",
        "channel": "c1",
        "data": {"to": "takeover", "triggered_by": "operator"},
    })
    await audit.on_ws_event({
        "event": "mode_changed",
        "channel": "c1",
        "data": {"to": "copilot", "triggered_by": "operator"},
    })
    status = audit.query("status", {"channel": "c1"})
    assert status["state"] == "active"
    assert status["takeovers"][0]["released_at"] is not None


@pytest.mark.asyncio
async def test_channel_resolved(audit):
    await audit.on_ws_event({"event": "channel_resolved", "channel": "c1", "data": {}})
    status = audit.query("status", {"channel": "c1"})
    assert status["state"] == "resolved"
    assert status["resolved_at"] is not None


@pytest.mark.asyncio
async def test_aggregates_escalation_resolve_rate(audit):
    # c1: takeover → resolve（成功升级结案）
    await audit.on_ws_event({"event": "mode_changed", "channel": "c1", "data": {"to": "takeover"}})
    await audit.on_ws_event({"event": "channel_resolved", "channel": "c1", "data": {}})
    # c2: takeover → 未 resolve
    await audit.on_ws_event({"event": "mode_changed", "channel": "c2", "data": {"to": "takeover"}})
    # c3: 无 takeover → resolve
    await audit.on_ws_event({"event": "channel_resolved", "channel": "c3", "data": {}})

    agg = audit._compute_aggregates()
    assert agg["total_takeovers"] == 2
    # takeover→resolve 只算升级转结案：1/2
    assert agg["escalation_resolve_rate"] == 0.5


def test_record_csat_stores_score(audit):
    audit.record_csat("c1", 5)
    status = audit.query("status", {"channel": "c1"})
    assert status["csat_score"] == 5


def test_csat_mean_aggregate(audit):
    audit.record_csat("c1", 5)
    audit.record_csat("c2", 3)
    agg = audit._compute_aggregates()
    assert agg["csat_mean"] == 4.0


@pytest.mark.asyncio
async def test_persistence_survives_reload(tmp_path):
    p = tmp_path / "audit.json"
    a = AuditPlugin(persist_path=p)
    await a.on_ws_event({"event": "mode_changed", "channel": "c1", "data": {"to": "takeover"}})
    await a.on_ws_message({"channel": "c1", "source": "customer", "content": "hi"})

    # 重新加载
    b = AuditPlugin(persist_path=p)
    status = b.query("status", {"channel": "c1"})
    assert status is not None
    assert status["state"] == "takeover"
    assert status["message_count"] == 1


def test_query_unknown_key(audit):
    assert audit.query("nonexistent") is None


def test_status_global(audit):
    result = audit.query("status")
    assert "channels" in result
    assert "aggregates" in result
