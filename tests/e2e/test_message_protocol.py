"""E2E 测试 IRC 消息协议：agent 发 IRC → channel-server 解析前缀 → Bridge API。

需要 ergo IRC server + channel-server 独立进程。
通过模拟 agent IRC 连接发送消息，验证 Bridge API WebSocket 收到正确路由的消息。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import pytest
import pytest_asyncio

import irc.client


@pytest.fixture
def agent_irc(ergo_server, e2e_ports):
    """模拟 agent 的 IRC 连接（发送消息到 #conv-* 频道）。"""
    reactor = irc.client.Reactor()
    conn = reactor.server().connect(
        "127.0.0.1", e2e_ports["irc"], f"test-agent-{os.getpid() % 1000}"
    )
    ready = False

    def on_welcome(c, e):
        nonlocal ready
        ready = True

    conn.add_global_handler("welcome", on_welcome)

    import threading
    t = threading.Thread(target=reactor.process_forever, daemon=True)
    t.start()

    deadline = time.time() + 10
    while not ready and time.time() < deadline:
        time.sleep(0.1)
    if not ready:
        pytest.fail("agent IRC connection timed out")

    yield conn

    try:
        conn.disconnect()
    except Exception:
        pass


async def _create_conversation(ws, conv_id: str) -> None:
    await ws.send(json.dumps({
        "type": "customer_connect",
        "conversation_id": conv_id,
        "customer": {"id": "customer-1", "name": "Test Customer"},
    }))
    while True:
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        if resp.get("type") == "customer_connected":
            break


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_edit_e2e_flow(channel_server, bridge_ws, agent_irc, e2e_ports) -> None:
    """TC-007: agent 发 __edit:msg_id:text → channel-server → Bridge API {type: "edit"}。"""
    conv_id = f"e2e-edit-{os.getpid() % 1000}"

    await _create_conversation(bridge_ws, conv_id)

    channel = f"#conv-{conv_id}"
    agent_irc.join(channel)
    await asyncio.sleep(1)

    msg_id = str(uuid.uuid4())
    agent_irc.privmsg(channel, f"__edit:{msg_id}:这是编辑后的内容")

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            data = json.loads(await asyncio.wait_for(bridge_ws.recv(), timeout=5))
            if data.get("type") == "edit":
                assert data["message_id"] == msg_id
                assert data["text"] == "这是编辑后的内容"
                assert data["conversation_id"] == conv_id
                return
        except asyncio.TimeoutError:
            break
    pytest.fail("Did not receive edit message on Bridge API")


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_side_e2e_flow(channel_server, bridge_ws, agent_irc, e2e_ports) -> None:
    """TC-008: agent 发 __side:text → channel-server → Bridge API {visibility: "side"}。"""
    conv_id = f"e2e-side-{os.getpid() % 1000}"

    await _create_conversation(bridge_ws, conv_id)

    channel = f"#conv-{conv_id}"
    agent_irc.join(channel)
    await asyncio.sleep(1)

    agent_irc.privmsg(channel, "__side:这是一条内部建议")

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            data = json.loads(await asyncio.wait_for(bridge_ws.recv(), timeout=5))
            if data.get("type") == "reply" and data.get("visibility") == "side":
                assert data["text"] == "这是一条内部建议"
                assert data["conversation_id"] == conv_id
                return
        except asyncio.TimeoutError:
            break
    pytest.fail("Did not receive side message on Bridge API")
