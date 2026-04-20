"""Bridge API WebSocket 传输层 — 连接 channel-server。

纯传输，不含协议语义或业务逻辑。负责：
1. WebSocket 连接生命周期（connect / reconnect / close）
2. 发送 JSON 消息
3. 接收 JSON 消息，通过 on_message 回调分发

协议语义和业务逻辑在 bridge.py 中处理。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable

import websockets

log = logging.getLogger("feishu-bridge.bridge_api_client")


class BridgeAPIClient:
    """channel-server Bridge API 的 WebSocket 传输客户端。"""

    def __init__(self, url: str, register_data: dict | None = None) -> None:
        self._url = url
        self._register_data = register_data or {}
        self._ws: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._reconnect_delay = 3.0

        # 接收回调（bridge.py 注入）
        self.on_message: Callable[[dict], None] | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    # ── 发送 ─────────────────────────────────────────────────────────

    def send(self, msg: dict) -> None:
        """线程安全地发送 JSON dict。"""
        if self._ws is None or self._loop is None:
            log.warning("not connected, dropping: %s", msg.get("type"))
            return
        raw = json.dumps(msg)
        fut = asyncio.run_coroutine_threadsafe(self._ws.send(raw), self._loop)
        try:
            fut.result(timeout=3)
        except Exception as e:
            log.exception("[send] WS send failed: %s (msg.type=%s, channel=%s)",
                          e, msg.get("type"), msg.get("channel"))

    # ── 连接生命周期 ─────────────────────────────────────────────────

    def start(self) -> None:
        """在后台线程启动连接（非阻塞）。"""
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._ws and self._loop:
            asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self) -> None:
        while self._running:
            try:
                await self._connect_once()
            except Exception:
                if not self._running:
                    return
                log.warning("disconnected, reconnecting in %.0fs", self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)

    async def _connect_once(self) -> None:
        async with websockets.connect(self._url) as ws:
            self._ws = ws
            # 注册（channel-server 只向已注册连接广播事件）
            if self._register_data:
                await ws.send(json.dumps(self._register_data))
                resp = await ws.recv()
                log.info("registered: %s", resp)
            log.info("connected to %s", self._url)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if self.on_message:
                    try:
                        self.on_message(msg)
                    except Exception:
                        log.exception("on_message callback error")
        self._ws = None
