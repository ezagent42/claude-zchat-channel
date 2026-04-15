"""E2E fixtures for channel-server Phase 4 tests.

启动真 ergo IRC server + channel-server 子进程。无 mock。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest
import pytest_asyncio
import websockets


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> None:
    """轮询端口可连直到 timeout。"""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError as e:
            last_err = e
            time.sleep(0.2)
    raise TimeoutError(f"port {host}:{port} not open in {timeout}s; last={last_err!r}")


@pytest.fixture(scope="session")
def e2e_ports() -> dict:
    base = 17000 + (os.getpid() % 500) * 3
    return {"irc": base, "bridge": base + 1}


@pytest.fixture(scope="session")
def ergo_bin() -> str:
    path = shutil.which("ergo")
    if not path:
        pytest.fail("ergo binary not on PATH — Phase 4 E2E requires ergo installed")
    return path


@pytest.fixture(scope="session")
def ergo_server(ergo_bin: str, e2e_ports: dict, tmp_path_factory) -> Iterator[subprocess.Popen]:
    workdir = tmp_path_factory.mktemp("ergo")
    conf = workdir / "ergo.yaml"
    conf.write_text(_ergo_yaml(e2e_ports["irc"], workdir), encoding="utf-8")

    # ergo 需要先 mkcerts 吗？不需要，我们不启 TLS。直接 run.
    # ergo initdb 一次（需要 database 文件）
    db_file = workdir / "ircd.db"
    subprocess.run(
        [ergo_bin, "initdb", "--conf", str(conf)],
        check=True,
        capture_output=True,
    )

    proc = subprocess.Popen(
        [ergo_bin, "run", "--conf", str(conf)],
        cwd=str(workdir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port("127.0.0.1", e2e_ports["irc"], timeout=10)
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _ergo_yaml(irc_port: int, workdir: Path) -> str:
    return f"""
network:
  name: ErgoTest

server:
  name: ergo.test
  listeners:
    "127.0.0.1:{irc_port}":
    "[::1]:{irc_port}":
  max-sendq: 96k
  connection-limits:
    enabled: true
    cidr-len-ipv4: 32
    cidr-len-ipv6: 64
    connections-per-subnet: 16
    exempted:
      - "localhost"
  connection-throttling:
    enabled: false

accounts:
  authentication-enabled: false
  registration:
    enabled: false
  multiclient:
    enabled: true
    allowed-by-default: true
  nick-reservation:
    enabled: false

channels:
  default-modes: +nt
  max-channels-per-client: 100
  registration:
    enabled: false

oper-classes:
  "server-admin":
    title: Server Admin
    capabilities:
      - "oper"

opers: {{}}

logging:
  - method: stderr
    level: error
    type: "* -userinput -useroutput"

datastore:
  path: {workdir / 'ircd.db'}

limits:
  nicklen: 32
  identlen: 20
  channellen: 64
  awaylen: 200
  kicklen: 390
  topiclen: 390
  monitor-entries: 100
  whowas-entries: 100
  chan-list-modes: 60
  linelen:
    rest: 2048

fakelag:
  enabled: false

history:
  enabled: false
"""


@pytest.fixture(scope="session")
def server_root() -> Path:
    """zchat-channel-server submodule 根目录。"""
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def channel_server(
    ergo_server,
    e2e_ports: dict,
    tmp_path: Path,
    server_root: Path,
) -> Iterator[subprocess.Popen]:
    """启动 channel-server 子进程（stdin piped, 永不输入）。"""
    env = {
        **os.environ,
        "IRC_SERVER": "127.0.0.1",
        "IRC_PORT": str(e2e_ports["irc"]),
        "IRC_CHANNELS": "general",
        "BRIDGE_PORT": str(e2e_ports["bridge"]),
        "AGENT_NAME": f"e2e-agent-{os.getpid() % 1000}",
        "CS_DB_PATH": str(tmp_path / "conv.db"),
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
async def bridge_ws(channel_server, e2e_ports: dict):
    """已注册的 Bridge WebSocket 连接（capabilities 覆盖三角色）。"""
    uri = f"ws://127.0.0.1:{e2e_ports['bridge']}"
    ws = await websockets.connect(uri)
    await ws.send(
        json.dumps(
            {
                "type": "register",
                "bridge_type": "test",
                "instance_id": f"e2e-test-{os.getpid()}",
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
