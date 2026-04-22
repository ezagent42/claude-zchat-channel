"""E2E: plugin 事件流水线串联验证。

场景：完整一个对话生命周期
  1. customer 发消息 → audit/activation 记录
  2. /hijack → mode plugin 切 takeover → audit 记 takeover + sla 启动 timer
  3. /release → mode plugin 切 copilot → audit 记释放
  4. /resolve → resolve plugin emit event → audit 记 resolved + activation 记 dormant
  5. 客户回访 → activation emit customer_returned event
  6. 验证 audit.json 完整轨迹
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from channel_server.plugin import PluginRegistry
from plugins.mode.plugin import ModePlugin
from plugins.resolve.plugin import ResolvePlugin
from plugins.audit.plugin import AuditPlugin
from plugins.activation.plugin import ActivationPlugin
from zchat_protocol import ws_messages


@pytest.fixture
def pipeline(tmp_path):
    """真实的 plugin registry 连接一起。"""
    registry = PluginRegistry()

    events_emitted: list[dict] = []

    async def emit_event(event: str, channel: str, data: dict) -> None:
        """mock emit_event，同时广播给所有 plugin（模拟 router.emit_event）。"""
        msg = ws_messages.build_event(channel, event, data)
        events_emitted.append(msg)
        await registry.broadcast_event(msg)

    mode = ModePlugin(config={}, emit_event=emit_event)
    resolve = ResolvePlugin(config={}, emit_event=emit_event)
    audit = AuditPlugin(config={"data_dir": str(tmp_path)})
    activation = ActivationPlugin(
        config={"data_dir": str(tmp_path)},
        emit_event=emit_event,
    )

    registry.register(mode)
    registry.register(resolve)
    registry.register(audit)
    registry.register(activation)

    return {
        "registry": registry,
        "mode": mode,
        "resolve": resolve,
        "audit": audit,
        "activation": activation,
        "emit_event": emit_event,
        "events": events_emitted,
        "tmp": tmp_path,
    }


@pytest.mark.asyncio
async def test_full_lifecycle(pipeline):
    """完整对话：消息 → hijack → release → resolve → 回访。"""
    registry = pipeline["registry"]
    mode = pipeline["mode"]
    resolve = pipeline["resolve"]
    audit = pipeline["audit"]
    activation = pipeline["activation"]

    channel = "conv-abc"

    # 1. 客户发消息
    await registry.broadcast_message({
        "type": "message",
        "channel": channel,
        "source": "customer",
        "content": "你好",
    })
    # agent 回复
    await registry.broadcast_message({
        "type": "message",
        "channel": channel,
        "source": "alice-fast-001",
        "content": "您好",
    })

    status = audit.query("status", {"channel": channel})
    assert status["message_count"] == 2
    assert status["first_reply_at"] is not None  # agent 回复记录
    assert activation.query("last_activity", {"channel": channel}) is not None

    # 2. /hijack
    await mode.on_command("hijack", {"channel": channel, "source": "operator"})
    status = audit.query("status", {"channel": channel})
    assert status["state"] == "takeover"
    assert len(status["takeovers"]) == 1
    assert audit._compute_aggregates()["total_takeovers"] == 1

    # 3. /release
    await mode.on_command("release", {"channel": channel, "source": "operator"})
    status = audit.query("status", {"channel": channel})
    assert status["state"] == "active"
    assert status["takeovers"][0]["released_at"] is not None

    # 4. /resolve
    await resolve.on_command("resolve", {"channel": channel, "source": "operator"})
    status = audit.query("status", {"channel": channel})
    assert status["state"] == "resolved"
    assert activation.query("is_dormant", {"channel": channel}) is True

    # 5. 客户回访
    returned_events_before = len([e for e in pipeline["events"] if e.get("event") == "customer_returned"])
    await registry.broadcast_message({
        "type": "message",
        "channel": channel,
        "source": "customer_xyz",
        "content": "还在吗？",
    })
    returned_events_after = len([e for e in pipeline["events"] if e.get("event") == "customer_returned"])
    assert returned_events_after == returned_events_before + 1

    # 6. 验证持久化（V7: 每个 plugin state 文件名固定 state.json；
    # V7 测试 fixture 把 data_dir 设为 tmp，所以文件叫 tmp/state.json；
    # 多 plugin 共用同 data_dir 时会覆盖——生产环境下每个 plugin 用独立子目录）
    audit_json = json.loads((pipeline["tmp"] / "state.json").read_text())
    assert channel in audit_json["channels"]


@pytest.mark.asyncio
async def test_escalation_resolve_rate_computation(pipeline):
    """takeover → resolve 的 channel 数 / takeover 总数 = 升级转结案率。"""
    registry = pipeline["registry"]
    mode = pipeline["mode"]
    resolve = pipeline["resolve"]
    audit = pipeline["audit"]

    # c1: takeover + resolve
    await mode.on_command("hijack", {"channel": "c1", "source": "op"})
    await resolve.on_command("resolve", {"channel": "c1", "source": "op"})
    # c2: takeover, no resolve
    await mode.on_command("hijack", {"channel": "c2", "source": "op"})
    # c3: resolve without takeover
    await resolve.on_command("resolve", {"channel": "c3", "source": "op"})

    agg = audit._compute_aggregates()
    assert agg["total_takeovers"] == 2
    assert agg["total_resolved"] == 2  # c1 + c3
    # takeover 里成功转 resolved: 只有 c1 → 1/2 = 0.5
    assert agg["escalation_resolve_rate"] == 0.5


@pytest.mark.asyncio
async def test_csat_score_recorded_via_audit(pipeline):
    """csat plugin 未注册，但 audit.record_csat 直接调用 OK。"""
    audit = pipeline["audit"]
    audit.record_csat("conv-1", 5)
    audit.record_csat("conv-2", 3)
    agg = audit._compute_aggregates()
    assert agg["csat_mean"] == 4.0
