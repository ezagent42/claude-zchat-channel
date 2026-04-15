"""E2E: Bridge 侧触发 operator_join / /hijack → mode.changed 事件广播验证。

测试完整路径：
  Bridge WS → BridgeAPIServer → on_operator_join callback → ModeManager →
  send_event() → Bridge WS recv
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_operator_join_triggers_copilot(bridge_ws, channel_server):
    """TC-E05: customer_connect → operator_join → Bridge 收到 mode.changed(auto→copilot)。

    验证：BridgeAPIServer.on_operator_join 注入后，ModeManager 做 auto→copilot
    转换，并通过 send_event() 广播 {type:"event", event_type:"mode.changed"} 到 Bridge。
    """
    conv_id = f"e2e_mode_{os.getpid()}_01"

    # 1. 创建对话（初始 auto 模式）
    await bridge_ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "david", "name": "David"},
            }
        )
    )
    # 消费 customer_connected 确认
    ack = json.loads(await asyncio.wait_for(bridge_ws.recv(), timeout=5))
    assert ack["type"] == "customer_connected"

    # 2. Operator 加入 → 触发 auto→copilot
    await bridge_ws.send(
        json.dumps(
            {
                "type": "operator_join",
                "conversation_id": conv_id,
                "operator": {"id": "xiaoli", "name": "小李"},
            }
        )
    )

    # 3. 收到 mode.changed 事件
    event = json.loads(await asyncio.wait_for(bridge_ws.recv(), timeout=5))
    assert event["type"] == "event", f"expected event, got: {event}"
    assert event["event_type"] == "mode.changed", f"wrong event_type: {event}"
    assert event["conversation_id"] == conv_id
    assert event["data"]["from"] == "auto"
    assert event["data"]["to"] == "copilot"


async def test_hijack_triggers_takeover(bridge_ws, channel_server):
    """TC-E06: operator_join 后 /hijack → Bridge 收到 mode.changed(copilot→takeover)。

    验证：on_operator_command 注入后，/hijack 命令触发 copilot→takeover 转换并广播。
    """
    conv_id = f"e2e_mode_{os.getpid()}_02"

    # 1. 创建对话
    await bridge_ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "david", "name": "David"},
            }
        )
    )
    # 消费 customer_connected 确认
    ack = json.loads(await asyncio.wait_for(bridge_ws.recv(), timeout=5))
    assert ack["type"] == "customer_connected"

    # 2. Operator 加入 → auto→copilot（消费 mode.changed 事件）
    await bridge_ws.send(
        json.dumps(
            {
                "type": "operator_join",
                "conversation_id": conv_id,
                "operator": {"id": "xiaoli", "name": "小李"},
            }
        )
    )
    copilot_event = json.loads(await asyncio.wait_for(bridge_ws.recv(), timeout=5))
    assert copilot_event["data"]["to"] == "copilot", (
        f"expected copilot event first, got: {copilot_event}"
    )

    # 3. /hijack → copilot→takeover
    await bridge_ws.send(
        json.dumps(
            {
                "type": "operator_command",
                "conversation_id": conv_id,
                "operator_id": "xiaoli",
                "command": "/hijack",
            }
        )
    )

    takeover_event = json.loads(await asyncio.wait_for(bridge_ws.recv(), timeout=5))
    assert takeover_event["type"] == "event", f"expected event, got: {takeover_event}"
    assert takeover_event["event_type"] == "mode.changed"
    assert takeover_event["data"]["from"] == "copilot"
    assert takeover_event["data"]["to"] == "takeover"
