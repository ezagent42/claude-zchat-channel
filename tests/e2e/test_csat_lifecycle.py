"""E2E: CSAT 完整生命周期。

场景（V6+）：
  1. operator 触发 /resolve → resolve plugin → emit channel_resolved
  2. csat plugin 订阅到 → emit csat_request → bridge 收到应发评分卡片
  3. 客户评分 → bridge emit `csat_score` event（走 event 通道，不走 message）
  4. csat plugin 订阅 csat_score → 调 audit.record_csat → audit.json 记录分数
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from channel_server.plugin import PluginRegistry
from plugins.audit.plugin import AuditPlugin
from plugins.csat.plugin import CsatPlugin
from plugins.resolve.plugin import ResolvePlugin
from zchat_protocol import ws_messages


@pytest.mark.asyncio
async def test_csat_full_lifecycle(tmp_path):
    """完整链路: resolve → csat_request → 评分 → audit 记录。"""
    registry = PluginRegistry()

    # 模拟 emit_event（同时广播给所有 plugin，类似 router.emit_event）
    events: list[dict] = []

    async def emit_event(event: str, channel: str, data: dict) -> None:
        msg = ws_messages.build_event(channel, event, data)
        events.append(msg)
        await registry.broadcast_event(msg)

    audit = AuditPlugin(persist_path=tmp_path / "audit.json")
    resolve = ResolvePlugin(emit_event=emit_event)
    csat = CsatPlugin(emit_event=emit_event, audit_plugin=audit)

    registry.register(audit)
    registry.register(resolve)
    registry.register(csat)

    # 1. operator /resolve
    await resolve.on_command("resolve", {"channel": "conv-csat", "source": "operator"})

    # 应该同时 emit channel_resolved + csat_request
    event_names = [e.get("event") for e in events]
    assert "channel_resolved" in event_names
    assert "csat_request" in event_names

    # 2. 客户评分（bridge 发的 csat_score event，走 event 通道 V6+）
    await emit_event("csat_score", "conv-csat", {"score": 5, "source": "customer"})

    # csat plugin 应该调用 audit.record_csat
    status = audit.query("status", {"channel": "conv-csat"})
    assert status["csat_score"] == 5

    # 应该 emit csat_recorded 事件
    event_names_after = [e.get("event") for e in events]
    assert "csat_recorded" in event_names_after


@pytest.mark.asyncio
async def test_csat_multiple_channels(tmp_path):
    """多个 channel 的 CSAT 独立累计。"""
    registry = PluginRegistry()

    async def emit_event(event: str, channel: str, data: dict) -> None:
        msg = ws_messages.build_event(channel, event, data)
        await registry.broadcast_event(msg)

    audit = AuditPlugin(persist_path=tmp_path / "audit.json")
    resolve = ResolvePlugin(emit_event=emit_event)
    csat = CsatPlugin(emit_event=emit_event, audit_plugin=audit)
    registry.register(audit)
    registry.register(resolve)
    registry.register(csat)

    # 3 个 channel 分别评分（V6+: csat_score event 通道）
    for ch, score in [("c1", 5), ("c2", 3), ("c3", 4)]:
        await resolve.on_command("resolve", {"channel": ch, "source": "op"})
        await emit_event("csat_score", ch, {"score": score, "source": "customer"})

    agg = audit._compute_aggregates()
    assert agg["csat_mean"] == pytest.approx(4.0, abs=0.01)
