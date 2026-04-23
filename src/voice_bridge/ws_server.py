"""HTTP + WebSocket server（浏览器入口）。

一个端口同时服务：
  - GET /                 → call.html（前端单页）
  - GET /static/<file>    → 前端 JS/CSS 等资源
  - GET /health           → ok
  - WS  /ws               → 浏览器 ↔ voice_bridge 的语音双向通道

使用 websockets ≥ 16 的 process_request(connection, request) API。

Phase 1：支持 dev-mode —— URL 上直接传 ?channel=&customer= 绕过 JWT。
Phase 3 会加 JWT 验签 (?t=JWT)，本文件预留 handler._parse_auth 扩展点。
"""
from __future__ import annotations

import http
import logging
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response
from websockets.datastructures import Headers

log = logging.getLogger(__name__)


# MIME types small table for static assets we actually serve
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".ico": "image/x-icon",
}


def _content_type(path: Path) -> str:
    return _MIME.get(path.suffix, "application/octet-stream")


def _response(status: http.HTTPStatus, body: bytes = b"", content_type: str = "text/plain") -> Response:
    headers = Headers([
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ])
    return Response(
        status_code=status.value,
        reason_phrase=status.phrase,
        headers=headers,
        body=body,
    )


class BrowserWSServer:
    """浏览器侧 HTTP+WS 服务。

    Args:
      on_ws_connect: coroutine callback(ws_connection, session_params)
        session_params = {"channel": str, "customer": str, "auth_mode": "dev"|"jwt"}
      static_dir: 前端静态文件目录
      bind_channel: dev mode 下若 URL 没带 channel 参数，fallback 到这个
      jwt_validator: Phase 3 injection point
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        static_dir: Path,
        on_ws_connect,
        dev_mode: bool = True,
        bind_channel: str = "",
        jwt_validator: Optional[Any] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._static = static_dir
        self._on_ws_connect = on_ws_connect
        self._dev_mode = dev_mode
        self._bind_channel = bind_channel.lstrip("#") if bind_channel else ""
        self._jwt_validator = jwt_validator
        self._server = None

    async def start(self) -> None:
        self._server = await serve(
            self._ws_handler,
            self._host, self._port,
            process_request=self._process_request,
            max_size=8 * 1024 * 1024,
            compression=None,
        )
        log.info("voice_bridge HTTP+WS listening on %s:%d (dev_mode=%s)",
                 self._host, self._port, self._dev_mode)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()

    # ------------------------------------------------------------------
    # HTTP interception (websockets 16 API)
    # ------------------------------------------------------------------

    def _process_request(
        self, connection: ServerConnection, request: Request,
    ) -> Optional[Response]:
        """Hook before WS upgrade. Return Response to short-circuit with HTTP reply;
        return None to allow WS handshake to proceed."""
        # Non-WS GET → serve static / health
        # websockets already dispatches WS upgrade when Upgrade: websocket present;
        # if it's there, return None and let the handler attach.
        upgrade = request.headers.get("Upgrade", "")
        if isinstance(upgrade, list):
            upgrade = upgrade[0] if upgrade else ""
        if upgrade.lower() == "websocket":
            return None

        parsed = urllib.parse.urlsplit(request.path)
        url_path = parsed.path
        if url_path in ("/", "/call"):
            return self._serve_static("call.html")
        if url_path.startswith("/static/"):
            filename = url_path[len("/static/"):]
            if "/" in filename or ".." in filename:
                return _response(http.HTTPStatus.FORBIDDEN, b"forbidden")
            return self._serve_static(filename)
        if url_path == "/health":
            return _response(http.HTTPStatus.OK, b"ok\n", "text/plain")
        return _response(http.HTTPStatus.NOT_FOUND, b"not found")

    def _serve_static(self, filename: str) -> Response:
        path = self._static / filename
        if not path.is_file():
            return _response(http.HTTPStatus.NOT_FOUND, b"not found")
        body = path.read_bytes()
        return _response(http.HTTPStatus.OK, body, _content_type(path))

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    async def _ws_handler(self, ws: ServerConnection) -> None:
        """Called after WS handshake completed."""
        # Access path from connection.request (populated after handshake)
        request_path = ws.request.path if ws.request else "/"
        query = _parse_query(request_path)
        auth = self._resolve_auth(query)
        if auth is None:
            await ws.close(code=1008, reason="unauthorized")
            return
        try:
            await self._on_ws_connect(ws, auth)
        except Exception as e:
            log.exception("ws handler error: %s", e)
            try:
                await ws.close(code=1011, reason="internal error")
            except Exception:
                pass

    def _resolve_auth(self, query: dict) -> dict | None:
        """Decide session identity from URL query.

        Returns:
          {"channel": str (bare), "customer": str, "auth_mode": "dev"|"jwt"}
          or None if rejected.
        """
        # JWT first (Phase 3)
        token = query.get("t", "")
        if token and self._jwt_validator is not None:
            claims = self._jwt_validator.validate(token)
            if claims is None:
                return None
            return {
                "channel": claims["channel"].lstrip("#"),
                "customer": claims["customer"],
                "auth_mode": "jwt",
            }
        # Dev mode
        if self._dev_mode:
            channel = query.get("channel", "") or self._bind_channel
            if not channel:
                return None
            return {
                "channel": channel.lstrip("#"),
                "customer": query.get("customer", "dev-user"),
                "auth_mode": "dev",
            }
        return None


def _parse_query(path: str) -> dict:
    """取 URL query dict；多值取首个。"""
    parsed = urllib.parse.urlsplit(path)
    return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items() if v}
