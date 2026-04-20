"""WebSocket server — 接受 bridge 注册 + 双向消息转发。

格式：zchat_protocol.ws_messages。
本模块不关心 content 语义，只做 transport 层的 JSON 解析 + 广播。
"""

from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import websockets
import websockets.exceptions

from zchat_protocol import ws_messages

log = logging.getLogger(__name__)


@dataclass
class BridgeConnection:
    instance_id: str
    bridge_type: str
    capabilities: list[str] = field(default_factory=list)
    websocket: Any = None


class WSServer:
    """WebSocket server — 管理 bridge 连接 + 广播入站消息到 handler。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9999,
        *,
        on_inbound: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_inbound = on_inbound
        self._connections: dict[str, BridgeConnection] = {}
        self._server: Any = None

    async def start(self) -> None:
        self._server = await websockets.serve(self._handler, self._host, self._port)
        log.info("[ws] listening on ws://%s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handler(self, websocket: Any) -> None:
        instance_id: str | None = None
        try:
            async for raw in websocket:
                log.info("[ws] received raw (%d bytes) from %s", len(raw), instance_id or "?")
                try:
                    msg = ws_messages.parse(raw)
                except (json.JSONDecodeError, ValueError) as e:
                    log.warning("[ws] invalid message: %s", e)
                    continue
                log.info("[ws] parsed type=%s channel=%s",
                         msg.get("type"), msg.get("channel"))

                if msg["type"] == ws_messages.WSType.REGISTER:
                    conn = BridgeConnection(
                        instance_id=msg["instance_id"],
                        bridge_type=msg["bridge_type"],
                        capabilities=msg.get("capabilities", []),
                        websocket=websocket,
                    )
                    self._connections[conn.instance_id] = conn
                    instance_id = conn.instance_id
                    await websocket.send(json.dumps({
                        "type": ws_messages.WSType.REGISTERED,
                        "instance_id": instance_id,
                    }))
                    log.info("[ws] bridge registered: %s (%s)", instance_id, conn.bridge_type)
                    continue

                # 其他消息交给上层 handler
                if self._on_inbound is not None:
                    try:
                        await self._on_inbound(msg)
                    except Exception as e:
                        log.exception("[ws] on_inbound error: %s", e)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if instance_id and instance_id in self._connections:
                del self._connections[instance_id]
                log.info("[ws] bridge disconnected: %s", instance_id)

    async def broadcast(self, msg: dict) -> None:
        """给所有已注册 bridge 发送消息。"""
        data = json.dumps(msg)
        for conn in list(self._connections.values()):
            if conn.websocket is None:
                continue
            try:
                await conn.websocket.send(data)
            except Exception:
                log.exception("[ws] broadcast failed to %s", conn.instance_id)

    @property
    def connection_count(self) -> int:
        """当前已注册 bridge 连接数。"""
        return len(self._connections)
