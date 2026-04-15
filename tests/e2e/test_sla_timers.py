"""E2E: SLA onboard timer 超时告警 (Task 4.6.7)。

测试路径：
  customer_connect → _on_customer_connect → plugin_manager.fire(on_conversation_created) →
  sla_app.on_conversation_created → TimerManager.set_timer(sla_onboard) →
  超时 → EventBus TIMER_EXPIRED → _on_sla_breach → bridge.send_event("sla.breach")
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Iterator

import pytest
import pytest_asyncio
import websockets


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"port {host}:{port} not open in {timeout}s")


@pytest.fixture
def channel_server_short_sla(
    ergo_server,
    e2e_ports: dict,
    tmp_path: Path,
    server_root: Path,
) -> Iterator[subprocess.Popen]:
    """启动 channel-server 并配置短 SLA（通过自定义 plugins dir）。"""
    # 创建临时 plugins dir，注入短时长的 sla_app
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "__init__.py").write_text("")
    sla_src = (server_root / "plugins" / "sla_app.py").read_text()
    # 把默认 3s 替换为 0.5s
    sla_src = sla_src.replace("SLA_ONBOARD_DURATION_S: float = 3.0",
                              "SLA_ONBOARD_DURATION_S: float = 0.5")
    (plugins_dir / "sla_app.py").write_text(sla_src)

    env = {
        **os.environ,
        "IRC_SERVER": "127.0.0.1",
        "IRC_PORT": str(e2e_ports["irc"]),
        "IRC_CHANNELS": "general",
        "BRIDGE_PORT": str(e2e_ports["bridge"]),
        "AGENT_NAME": f"e2e-sla-{os.getpid() % 1000}",
        "CS_DB_PATH": str(tmp_path / "conv.db"),
        "CS_PLUGINS_DIR": str(plugins_dir),
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
async def bridge_ws_sla(channel_server_short_sla, e2e_ports: dict):
    uri = f"ws://127.0.0.1:{e2e_ports['bridge']}"
    ws = await websockets.connect(uri)
    await ws.send(
        json.dumps(
            {
                "type": "register",
                "bridge_type": "test",
                "instance_id": f"e2e-sla-test-{os.getpid()}",
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


async def test_sla_onboard_breach_e2e(bridge_ws_sla):
    """TC-E01: customer_connect → 0.5s 后未应答 → 收到 sla.breach event + admin alert。"""
    conv_id = f"e2e_sla_{os.getpid()}_01"

    await bridge_ws_sla.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "alice", "name": "Alice"},
            }
        )
    )
    ack = json.loads(await asyncio.wait_for(bridge_ws_sla.recv(), timeout=5))
    assert ack["type"] == "customer_connected"

    # 收集后续消息（等 sla_onboard 超时）
    breach_event = None
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(bridge_ws_sla.recv(), timeout=2.0)
            m = json.loads(raw)
            if m.get("type") == "event" and m.get("event_type") == "sla.breach":
                if m.get("data", {}).get("breach_type") == "sla_onboard":
                    breach_event = m
                    break
        except asyncio.TimeoutError:
            continue

    assert breach_event is not None, "expected sla.breach event with breach_type=sla_onboard"
    assert breach_event["data"]["conversation_id"] == conv_id
    assert breach_event["data"]["breach_type"] == "sla_onboard"
