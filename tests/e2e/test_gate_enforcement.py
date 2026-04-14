"""E2E: Gate Enforcement — visibility 路由验证。

测试 BridgeAPIServer.send_reply() 的 visibility 过滤：
- side / system → 只到 operator/admin bridge，不到 customer bridge
- mode.changed event → 广播到所有 bridge（无 visibility 过滤）

使用两个独立的 WebSocket 连接模拟 customer side 和 operator side。
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio
import websockets


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def customer_ws(channel_server, e2e_ports):
    """仅有 customer capability 的 Bridge 连接。"""
    uri = f"ws://127.0.0.1:{e2e_ports['bridge']}"
    ws = await websockets.connect(uri)
    instance_id = f"e2e-customer-{os.getpid()}"
    await ws.send(
        json.dumps(
            {
                "type": "register",
                "bridge_type": "test-customer",
                "instance_id": instance_id,
                "capabilities": ["customer"],
            }
        )
    )
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ack["type"] == "registered"
    try:
        yield ws
    finally:
        await ws.close()


@pytest_asyncio.fixture
async def operator_ws(channel_server, e2e_ports):
    """仅有 operator capability 的 Bridge 连接。"""
    uri = f"ws://127.0.0.1:{e2e_ports['bridge']}"
    ws = await websockets.connect(uri)
    instance_id = f"e2e-operator-{os.getpid()}"
    await ws.send(
        json.dumps(
            {
                "type": "register",
                "bridge_type": "test-operator",
                "instance_id": instance_id,
                "capabilities": ["operator"],
            }
        )
    )
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ack["type"] == "registered"
    try:
        yield ws
    finally:
        await ws.close()


async def _setup_takeover(ws, conv_id: str) -> None:
    """辅助：创建对话 → operator_join(copilot) → /hijack(takeover)。消费两个 mode.changed 事件。"""
    await ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "david", "name": "David"},
            }
        )
    )
    await asyncio.sleep(0.3)
    await ws.send(
        json.dumps(
            {
                "type": "operator_join",
                "conversation_id": conv_id,
                "operator": {"id": "xiaoli", "name": "小李"},
            }
        )
    )
    # 消费 auto→copilot 事件
    e1 = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert e1["data"]["to"] == "copilot", f"expected copilot event, got: {e1}"

    await ws.send(
        json.dumps(
            {
                "type": "operator_command",
                "conversation_id": conv_id,
                "operator_id": "xiaoli",
                "command": "/hijack",
            }
        )
    )
    # 消费 copilot→takeover 事件
    e2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert e2["data"]["to"] == "takeover", f"expected takeover event, got: {e2}"


async def test_side_message_not_received_by_customer(
    customer_ws, operator_ws, channel_server
):
    """TC-E07: takeover 模式下 side 消息不到 customer bridge。

    流程：
    1. 用 operator_ws 驱动对话进入 takeover 模式（同时广播 mode.changed 到所有 bridge）
    2. 消费 customer_ws 积压的 mode.changed 广播事件（它们是协议级事件，客户也能看到）
    3. /hijack callback 额外发出 side visibility 系统通知
    4. operator_ws 应收到该 side 消息；customer_ws 不应收到

    验证 compute_visibility_targets("side") = {operator, admin} 的端到端效果。
    """
    conv_id = f"e2e_gate_{os.getpid()}_01"

    # 1. operator_ws 驱动 auto→copilot→takeover（消费 operator_ws 的 mode.changed 事件）
    await _setup_takeover(operator_ws, conv_id)

    # 2. 清空 customer_ws 积压的 mode.changed 广播事件
    #    setup 过程中 send_event() 向所有连接广播了 2 个事件（auto→copilot, copilot→takeover）
    for _ in range(2):
        try:
            queued = json.loads(await asyncio.wait_for(customer_ws.recv(), timeout=2.0))
            assert queued.get("event_type") == "mode.changed", (
                f"expected mode.changed drain event, got: {queued}"
            )
        except asyncio.TimeoutError:
            break  # 少于预期事件数也可接受

    # 3. /hijack callback 发出的 side 系统通知：operator_ws 应收到
    side_msg = json.loads(await asyncio.wait_for(operator_ws.recv(), timeout=5))
    assert side_msg.get("visibility") == "side", (
        f"operator_ws should receive side message; got: {side_msg}"
    )

    # 4. customer_ws 此后不应再收到任何消息（side 过滤生效）
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(customer_ws.recv(), timeout=3.0)


async def test_mode_changed_event_broadcast_to_all(
    customer_ws, operator_ws, channel_server
):
    """TC-E08: mode.changed event 广播到所有 bridge（含 customer capability）。

    事件是协议级状态通知，不受 visibility 过滤，应广播到所有注册连接。
    """
    conv_id = f"e2e_gate_{os.getpid()}_02"

    # 双发 customer_connect（两个 ws 都接收 mode.changed）
    # 通过 operator_ws 触发 operator_join
    await customer_ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "david", "name": "David"},
            }
        )
    )
    await asyncio.sleep(0.3)

    await operator_ws.send(
        json.dumps(
            {
                "type": "operator_join",
                "conversation_id": conv_id,
                "operator": {"id": "xiaoli", "name": "小李"},
            }
        )
    )

    # 两个 bridge 都应收到 mode.changed 事件
    op_event = json.loads(await asyncio.wait_for(operator_ws.recv(), timeout=5))
    cust_event = json.loads(await asyncio.wait_for(customer_ws.recv(), timeout=5))

    assert op_event["event_type"] == "mode.changed", (
        f"operator_ws should get mode.changed; got: {op_event}"
    )
    assert cust_event["event_type"] == "mode.changed", (
        f"customer_ws should get mode.changed; got: {cust_event}"
    )
    assert op_event["data"]["to"] == "copilot"
    assert cust_event["data"]["to"] == "copilot"
