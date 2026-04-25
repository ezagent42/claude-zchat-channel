"""HTTP + WebSocket server（浏览器入口）。

一个端口同时服务：
  - GET /health           → ok
  - GET /issue?channel=&customer=&ttl=  → 签 JWT，返回 {url, expires_at}
  - WS  /ws?t=<JWT>       → 浏览器 ↔ voice_bridge 的语音双向通道
  - GET /  /call /static/* → call.html demo 页面（serve_static=True 时）

使用 websockets ≥ 16 的 process_request(connection, request) API。
"""
from __future__ import annotations

import http
import json
import logging
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response
from websockets.datastructures import Headers

from .tokens import issue_token

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
      jwt_validator: 入站 /ws 校验
      jwt_secret: 出站 /issue 签发；空 → /issue 返回 503
      public_ws_url_template: /issue 返回的 URL 模板。默认空 → 用 request 的
        Host 头自动拼。模板里 %s 会被 token 替换。例：
          "wss://voice.example.com/ws?t=%s"
        留空时拼成 "ws://<Host>/ws?t=<token>"。
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
        serve_static: bool = True,
        jwt_secret: str = "",
        public_ws_url_template: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._static = static_dir
        self._on_ws_connect = on_ws_connect
        self._dev_mode = dev_mode
        self._bind_channel = bind_channel.lstrip("#") if bind_channel else ""
        self._jwt_validator = jwt_validator
        self._serve_static = serve_static
        self._jwt_secret = jwt_secret or ""
        self._public_ws_url_template = public_ws_url_template or ""
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
        # 宽容：URL 复制带前后空格/%20 也能落到根路径
        url_path = parsed.path.strip().rstrip("/") or "/"
        if url_path == "/health":
            return _response(http.HTTPStatus.OK, b"ok\n", "text/plain")
        if url_path == "/issue":
            return self._handle_issue(request, parsed.query)
        # serve_static=False：关闭 fallback call.html，只留 /ws + /health + /issue
        # 适合自家 web 集成场景（前端自己连 /ws?t=<JWT>）
        if not self._serve_static:
            return _response(http.HTTPStatus.NOT_FOUND, b"not found")
        if url_path in ("/", "/call"):
            return self._serve_static_file("call.html")
        if url_path.startswith("/static/"):
            filename = url_path[len("/static/"):]
            if "/" in filename or ".." in filename:
                return _response(http.HTTPStatus.FORBIDDEN, b"forbidden")
            return self._serve_static_file(filename)
        return _response(http.HTTPStatus.NOT_FOUND, b"not found")

    def _handle_issue(self, request: Request, query_str: str) -> Response:
        """GET /issue?channel=&customer=&ttl= → JSON {url, expires_at}.

        用于 agent_mcp 的 voice_link tool。jwt_secret 内化在 voice_bridge，
        agent / plugin / 任何外部都不持有。
        """
        if not self._jwt_secret:
            return _response(
                http.HTTPStatus.SERVICE_UNAVAILABLE,
                b'{"error":"voice_bridge has no jwt_secret configured"}\n',
                "application/json",
            )
        params = {k: v[0] for k, v in urllib.parse.parse_qs(query_str).items() if v}
        channel = params.get("channel", "").strip()
        customer = params.get("customer", "").strip()
        if not channel or not customer:
            return _response(
                http.HTTPStatus.BAD_REQUEST,
                b'{"error":"channel and customer are required"}\n',
                "application/json",
            )
        try:
            ttl = int(params.get("ttl", "180"))
        except ValueError:
            ttl = 180
        ttl = max(30, min(900, ttl))

        token = issue_token(
            channel=channel.lstrip("#"),
            customer=customer,
            secret=self._jwt_secret,
            ttl_seconds=ttl,
        )

        # URL 模板：优先用配置；否则用 request Host 拼 ws://<Host>/ws?t=<token>
        if self._public_ws_url_template:
            if "%s" in self._public_ws_url_template:
                url = self._public_ws_url_template % token
            else:
                sep = "&" if "?" in self._public_ws_url_template else "?"
                url = f"{self._public_ws_url_template}{sep}t={token}"
        else:
            host_hdr = request.headers.get("Host", f"{self._host}:{self._port}")
            if isinstance(host_hdr, list):
                host_hdr = host_hdr[0] if host_hdr else f"{self._host}:{self._port}"
            url = f"ws://{host_hdr}/ws?t={token}"

        body = json.dumps({
            "url": url,
            "expires_at": int(time.time()) + ttl,
        }).encode("utf-8") + b"\n"
        return _response(http.HTTPStatus.OK, body, "application/json")

    def _serve_static_file(self, filename: str) -> Response:
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
