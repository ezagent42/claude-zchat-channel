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
