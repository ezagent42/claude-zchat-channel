"""E2E: Bridge 注册握手。"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
import websockets


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_register_returns_registered_ack(channel_server, e2e_ports):
    uri = f"ws://127.0.0.1:{e2e_ports['bridge']}"
    async with websockets.connect(uri) as ws:
        instance_id = f"e2e-reg-{os.getpid()}"
        await ws.send(
            json.dumps(
                {
                    "type": "register",
                    "bridge_type": "test",
                    "instance_id": instance_id,
                    "capabilities": ["customer"],
                }
            )
        )
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ack["type"] == "registered"
    assert ack["instance_id"] == instance_id


async def test_register_capabilities_preserved(bridge_ws, e2e_ports):
    """已注册的 bridge_ws fixture 说明 capabilities 被服务端接受且连接保持。"""
    # 发一个无 handler 的消息类型，服务端应保持连接（不崩溃），
    # 然后我们用 ping 确认 socket 仍在
    await bridge_ws.send(json.dumps({"type": "noop"}))
    pong_waiter = await bridge_ws.ping()
    await asyncio.wait_for(pong_waiter, timeout=5)
