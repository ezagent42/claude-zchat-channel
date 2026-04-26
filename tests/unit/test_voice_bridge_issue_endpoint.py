"""voice_bridge HTTP endpoint：GET /issue?channel=&customer=&ttl=

agent_mcp 的 voice_link tool 调它拿 URL。jwt_secret 内化在 voice_bridge，
plugin / agent 都不持有 secret。
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from voice_bridge.config import VoiceBridgeConfig
from voice_bridge.tokens import JWTValidator, validate_token
from voice_bridge.ws_server import BrowserWSServer


_SECRET = "test-secret-32-bytes-min-for-hs256-ok"


async def _start_server(*, public_url: str = "") -> tuple[BrowserWSServer, int]:
    """Start server bound to ephemeral port; return (server, actual_port)."""
    validator = JWTValidator(secret=_SECRET)

    async def _on_ws(_ws, _auth):
        await _ws.close()

    server = BrowserWSServer(
        host="127.0.0.1", port=0,
        static_dir=Path(__file__).parent,  # 不重要，serve_static=False
        on_ws_connect=_on_ws,
        jwt_validator=validator,
        serve_static=False,
        jwt_secret=_SECRET,
        public_ws_url_template=public_url,
    )
    await server.start()
    sockets = server._server.sockets  # type: ignore[attr-defined]
    port = sockets[0].getsockname()[1]
    return server, port


def _http_get(port: int, path: str) -> tuple[int, dict, str]:
    """Synchronous HTTP GET via stdlib (run in thread to not block loop)."""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8")


@pytest.mark.asyncio
async def test_issue_returns_url_with_jwt():
    server, port = await _start_server()
    try:
        status, _hdrs, body = await asyncio.to_thread(
            _http_get, port,
            "/issue?channel=conv-001&customer=alice&ttl=60",
        )
        assert status == 200, body
        data = json.loads(body)
        assert "url" in data and "expires_at" in data

        # URL 形如 "ws://host:port/ws?t=<JWT>"
        parsed = urllib.parse.urlsplit(data["url"])
        qs = dict(urllib.parse.parse_qsl(parsed.query))
        token = qs.get("t", "")
        assert token, f"no t= in {data['url']}"

        claims = validate_token(token, secret=_SECRET)
        assert claims.channel == "conv-001"
        assert claims.customer == "alice"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_issue_strips_channel_hash():
    server, port = await _start_server()
    try:
        status, _hdrs, body = await asyncio.to_thread(
            _http_get, port, "/issue?channel=%23room&customer=bob",
        )
        assert status == 200
        data = json.loads(body)
        token = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(data["url"]).query))["t"]
        claims = validate_token(token, secret=_SECRET)
        assert claims.channel == "room"  # leading '#' stripped
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_issue_rejects_missing_params():
    server, port = await _start_server()
    try:
        status, _hdrs, _body = await asyncio.to_thread(
            _http_get, port, "/issue?customer=alice",
        )
        assert status == 400
        status, _hdrs, _body = await asyncio.to_thread(
            _http_get, port, "/issue?channel=c",
        )
        assert status == 400
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_issue_uses_default_ttl_when_omitted():
    server, port = await _start_server()
    try:
        status, _hdrs, body = await asyncio.to_thread(
            _http_get, port, "/issue?channel=c&customer=u",
        )
        assert status == 200
        data = json.loads(body)
        token = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(data["url"]).query))["t"]
        claims = validate_token(token, secret=_SECRET)
        # exp - iat 应在合理范围（默认 180s）
        assert 60 <= claims.exp - claims.iat <= 900
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_issue_loopback_only_default_accepts_127_0_0_1():
    """Default: /issue 只接 127.0.0.1 → 同主机 GET 应通"""
    server, port = await _start_server()
    try:
        status, _hdrs, body = await asyncio.to_thread(
            _http_get, port, "/issue?channel=c&customer=u",
        )
        assert status == 200, body
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_issue_loopback_only_default_rejects_lan():
    """LAN 模拟：构造 server with non-loopback peer 应被拒"""
    # 直接调内部方法，模拟非 loopback 连接
    from unittest.mock import MagicMock
    from voice_bridge.tokens import JWTValidator
    from voice_bridge.ws_server import BrowserWSServer

    async def _on_ws(_ws, _auth):
        await _ws.close()

    server = BrowserWSServer(
        host="127.0.0.1", port=0,
        static_dir=Path(__file__).parent,
        on_ws_connect=_on_ws,
        jwt_validator=JWTValidator(secret=_SECRET),
        serve_static=False,
        jwt_secret=_SECRET,
    )
    fake_conn = MagicMock()
    fake_conn.remote_address = ("192.168.1.50", 54321)
    fake_request = MagicMock()
    fake_request.headers = {"Host": "voice.example.com"}
    fake_request.path = "/issue?channel=c&customer=u"
    resp = server._process_request(fake_conn, fake_request)
    assert resp is not None
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_issue_loopback_only_can_be_disabled():
    """issue_loopback_only=False → 允许任意 IP"""
    from unittest.mock import MagicMock
    from voice_bridge.tokens import JWTValidator
    from voice_bridge.ws_server import BrowserWSServer

    async def _on_ws(_ws, _auth):
        await _ws.close()

    server = BrowserWSServer(
        host="127.0.0.1", port=0,
        static_dir=Path(__file__).parent,
        on_ws_connect=_on_ws,
        jwt_validator=JWTValidator(secret=_SECRET),
        serve_static=False,
        jwt_secret=_SECRET,
        issue_loopback_only=False,
    )
    fake_conn = MagicMock()
    fake_conn.remote_address = ("192.168.1.50", 54321)
    fake_request = MagicMock()
    fake_request.headers = {"Host": "voice.example.com"}
    fake_request.path = "/issue?channel=c&customer=u"
    resp = server._process_request(fake_conn, fake_request)
    assert resp is not None
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ws_path_not_subject_to_loopback_filter():
    """/ws 不受 issue_loopback_only 影响 — 公网浏览器要能连"""
    from unittest.mock import MagicMock
    from voice_bridge.tokens import JWTValidator
    from voice_bridge.ws_server import BrowserWSServer

    async def _on_ws(_ws, _auth):
        await _ws.close()

    server = BrowserWSServer(
        host="127.0.0.1", port=0,
        static_dir=Path(__file__).parent,
        on_ws_connect=_on_ws,
        jwt_validator=JWTValidator(secret=_SECRET),
        serve_static=False,
        jwt_secret=_SECRET,
        # default issue_loopback_only=True
    )
    fake_conn = MagicMock()
    fake_conn.remote_address = ("203.0.113.42", 12345)  # public IP
    fake_request = MagicMock()
    fake_request.headers = {"Upgrade": "websocket"}
    fake_request.path = "/ws?t=ignore"
    resp = server._process_request(fake_conn, fake_request)
    # WS upgrade → return None (let websockets handshake proceed)
    assert resp is None


@pytest.mark.asyncio
async def test_issue_disabled_when_no_secret():
    """没有 jwt_secret 时 /issue 应返回 503 — 不能签 token 直接拒"""
    async def _on_ws(_ws, _auth):
        await _ws.close()
    server = BrowserWSServer(
        host="127.0.0.1", port=0,
        static_dir=Path(__file__).parent,
        on_ws_connect=_on_ws,
        jwt_validator=None,
        serve_static=False,
        jwt_secret="",
        public_ws_url_template="",
    )
    await server.start()
    try:
        port = server._server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]
        status, _hdrs, _body = await asyncio.to_thread(
            _http_get, port, "/issue?channel=c&customer=u",
        )
        assert status == 503
    finally:
        await server.stop()
