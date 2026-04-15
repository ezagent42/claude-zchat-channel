"""E2E 测试 routing 配置: auto-dispatch + 白名单验证。

需要 ergo IRC server + channel-server 独立进程。
channel-server 使用 routing.toml 配置文件启动（通过 CS_ROUTING_CONFIG 环境变量）。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Iterator

import socket

import pytest
import pytest_asyncio
import websockets


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"port {host}:{port} not open in {timeout}s")


@pytest.fixture
def routing_toml(tmp_path) -> str:
    """创建测试用 routing.toml。"""
    config = tmp_path / "routing.toml"
    config.write_text(
        """
[routing]
default_agents = ["auto-agent"]
escalation_chain = ["deep-agent", "operator"]
available_agents = ["auto-agent", "deep-agent", "manual-agent"]
"""
    )
    return str(config)


@pytest.fixture
def channel_server_with_routing(
    ergo_server, e2e_ports, tmp_path, server_root, routing_toml
) -> Iterator[subprocess.Popen]:
    """启动带 routing 配置的 channel-server。"""
    env = {
        **os.environ,
        "IRC_SERVER": "127.0.0.1",
        "IRC_PORT": str(e2e_ports["irc"]),
        "IRC_CHANNELS": "general",
        "BRIDGE_PORT": str(e2e_ports["bridge"]),
        "AGENT_NAME": f"cs-routing-{os.getpid() % 1000}",
        "CS_DB_PATH": str(tmp_path / "conv.db"),
        "CS_ROUTING_CONFIG": routing_toml,
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        ["uv", "run", "python", "-m", "server"],
        cwd=str(server_root),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port("127.0.0.1", e2e_ports["bridge"], timeout=20)
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest_asyncio.fixture
async def routing_ws(channel_server_with_routing, e2e_ports):
    """已注册的 Bridge WebSocket 连接（用于 routing 测试）。"""
    uri = f"ws://127.0.0.1:{e2e_ports['bridge']}"
    ws = await websockets.connect(uri)
    await ws.send(
        json.dumps(
            {
                "type": "register",
                "bridge_type": "test",
                "instance_id": f"routing-test-{os.getpid()}",
                "capabilities": ["customer", "operator", "admin"],
            }
        )
    )
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ack["type"] == "registered"
    try:
        yield ws
    finally:
        await ws.close()


async def test_auto_dispatch_on_create(routing_ws) -> None:
    """TC-R01: customer_connect → default_agents 自动 dispatch → 收到 agent.dispatched。"""
    ws = routing_ws
    conv_id = f"e2e_routing_{os.getpid()}_01"

    await ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "alice", "name": "Alice"},
            }
        )
    )

    # 收集消息：customer_connected + agent.dispatched
    msgs = []
    for _ in range(5):
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            msgs.append(json.loads(raw))
        except asyncio.TimeoutError:
            break

    dispatch_events = [
        m for m in msgs
        if m.get("type") == "event" and m.get("event_type") == "agent.dispatched"
    ]
    assert len(dispatch_events) >= 1, f"expected agent.dispatched event, got: {msgs}"
    assert dispatch_events[0]["data"]["agent_nick"] == "auto-agent"
    assert dispatch_events[0]["data"]["dispatched_by"] == "__auto"


async def _drain_until_dispatched(ws, timeout: float = 10.0) -> None:
    """排空消息直到收到 agent.dispatched 事件（auto-dispatch 完成标志）。"""
    import time as _time
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=3)
            msg = json.loads(raw)
            if msg.get("event_type") == "agent.dispatched":
                return
        except asyncio.TimeoutError:
            continue
    # timeout 也可接受（无 routing config 时不会有 auto-dispatch）


async def test_dispatch_whitelist_reject_e2e(routing_ws) -> None:
    """TC-R02: /dispatch agent 不在白名单 → 收到拒绝 reply。"""
    ws = routing_ws
    conv_id = f"e2e_routing_{os.getpid()}_02"

    # 创建对话 + 排空到 auto-dispatch 完成
    await ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "bob", "name": "Bob"},
            }
        )
    )
    await _drain_until_dispatched(ws)

    # /dispatch 不在白名单的 agent
    await ws.send(
        json.dumps(
            {
                "type": "admin_command",
                "conversation_id": "__admin",
                "admin_id": "boss",
                "command": f"/dispatch {conv_id} rogue-agent",
            }
        )
    )

    raw = await asyncio.wait_for(ws.recv(), timeout=5)
    msg = json.loads(raw)
    assert msg["type"] == "reply", f"expected reply, got: {msg}"
    assert "rejected" in msg["text"], f"expected rejection, got: {msg}"
    assert "rogue-agent" in msg["text"]


async def test_dispatch_whitelist_pass_e2e(routing_ws) -> None:
    """TC-R03: /dispatch agent 在白名单 → 收到 agent.dispatched event。"""
    ws = routing_ws
    conv_id = f"e2e_routing_{os.getpid()}_03"

    # 创建对话 + 排空到 auto-dispatch 完成
    await ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "charlie", "name": "Charlie"},
            }
        )
    )
    await _drain_until_dispatched(ws)

    # /dispatch 在白名单的 agent
    await ws.send(
        json.dumps(
            {
                "type": "admin_command",
                "conversation_id": "__admin",
                "admin_id": "boss",
                "command": f"/dispatch {conv_id} manual-agent",
            }
        )
    )

    raw = await asyncio.wait_for(ws.recv(), timeout=5)
    msg = json.loads(raw)
    assert msg["type"] == "event", f"expected event, got: {msg}"
    assert msg["event_type"] == "agent.dispatched"
    assert msg["data"]["agent_nick"] == "manual-agent"
