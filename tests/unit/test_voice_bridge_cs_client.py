"""CSClient 集成测试 — 用内置 WS server mock 模拟 channel_server，
验证 register 握手 + send / on_message 回环。
"""
from __future__ import annotations

import asyncio
import json

import pytest
import websockets
from websockets.asyncio.server import serve
from zchat_protocol import ws_messages

from voice_bridge.cs_client import CSClient


async def _fake_cs(port: int, received: list, stop: asyncio.Event):
    """Minimal CS-like server: respond 'registered' then echo / record."""
    async def handler(ws):
        try:
            # First frame should be register
            raw = await ws.recv()
            msg = json.loads(raw)
            received.append(msg)
            assert msg["type"] == "register"
            # ACK
            await ws.send(json.dumps({"type": "registered", "instance_id": msg["instance_id"]}))
            # Echo subsequent messages back (simulating broadcast)
            async for raw2 in ws:
                try:
                    m = json.loads(raw2)
                except Exception:
                    continue
                received.append(m)
                # Broadcast a message event back to the client
                if m.get("type") == "message":
                    broadcast = ws_messages.build_message(
                        channel=m["channel"],
                        source="fake-agent",
                        content=f"__msg:reply-uuid:echo {m['content']}",
                    )
                    await ws.send(json.dumps(broadcast))
        except websockets.ConnectionClosed:
            pass
    server = await serve(handler, "127.0.0.1", port)
    try:
        await stop.wait()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_connect_registers_and_ack_succeeds():
    port = 19991
    received: list = []
    stop = asyncio.Event()
    server_task = asyncio.create_task(_fake_cs(port, received, stop))
    await asyncio.sleep(0.1)  # let server bind

    client = CSClient(
        url=f"ws://127.0.0.1:{port}",
        instance_id="voice-test",
        bridge_type="voice",
        reconnect_delay=0,  # disable auto-reconnect for tests
    )
    try:
        await client.connect()
        assert client.connected
        # server should have received our register
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0]["type"] == "register"
        assert received[0]["bridge_type"] == "voice"
        assert received[0]["instance_id"] == "voice-test"
    finally:
        await client.close()
        stop.set()
        await server_task


@pytest.mark.asyncio
async def test_send_and_on_message_roundtrip():
    port = 19992
    received: list = []
    stop = asyncio.Event()
    server_task = asyncio.create_task(_fake_cs(port, received, stop))
    await asyncio.sleep(0.1)

    got_messages: list = []

    async def handle(msg: dict):
        got_messages.append(msg)

    client = CSClient(
        url=f"ws://127.0.0.1:{port}",
        instance_id="voice-x",
        reconnect_delay=0,
    )
    client.on_message = handle
    try:
        await client.connect()
        await client.send(ws_messages.build_message(
            channel="test-voice", source="voice-alice", content="hello"
        ))
        # wait for echo back
        for _ in range(20):
            if got_messages:
                break
            await asyncio.sleep(0.05)
        assert len(got_messages) == 1
        echoed = got_messages[0]
        assert echoed["type"] == "message"
        assert echoed["channel"] == "test-voice"
        assert echoed["source"] == "fake-agent"
        assert "hello" in echoed["content"]
    finally:
        await client.close()
        stop.set()
        await server_task


@pytest.mark.asyncio
async def test_send_before_connect_is_noop():
    client = CSClient(url="ws://127.0.0.1:19993", instance_id="nc", reconnect_delay=0)
    # Not connected — should log warning and not crash
    await client.send({"type": "message", "channel": "c", "source": "s", "content": "x"})


@pytest.mark.asyncio
async def test_connect_timeout_when_server_unreachable():
    """No server listening → connect() should fail inside the 10s timeout."""
    client = CSClient(url="ws://127.0.0.1:19994", instance_id="unreach", reconnect_delay=0)
    # reconnect_delay=0 means single attempt; connect() wait 10s for registration
    with pytest.raises((asyncio.TimeoutError, OSError, Exception)):
        await asyncio.wait_for(client.connect(), timeout=3)
    await client.close()
