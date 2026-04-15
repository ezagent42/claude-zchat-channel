"""Bridge API WebSocket server (spec 02-channel-server §5).

对 Bridge 层暴露 WebSocket 接口，接收客户/客服/管理员三种角色消息，
按 visibility 规则路由回复到对应 Bridge 端。

IRC 是内部 transport；所有人类用户都通过 Bridge API 接入 channel-server。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import websockets
from websockets.legacy.server import WebSocketServerProtocol  # 兼容旧版 API

from protocol.commands import Command, parse_command

logger = logging.getLogger(__name__)


# visibility → 目标角色集合（spec §5 路由表）
_VISIBILITY_ROUTING: dict[str, frozenset[str]] = {
    "public": frozenset({"customer", "operator", "admin"}),
    "side": frozenset({"operator", "admin"}),
    "system": frozenset({"operator", "admin"}),
}


@dataclass
class BridgeConnection:
    """一个已注册的 Bridge WebSocket 连接。"""

    bridge_type: str
    instance_id: str
    capabilities: list[str] = field(default_factory=list)
    websocket: WebSocketServerProtocol | None = None


class BridgeAPIServer:
    """WebSocket server + 消息路由。

    只做 transport 层的解析和分发：
    - 解析 register / customer_* / operator_* / admin_* 消息
    - 调用 ConversationManager / 命令处理器等上层协程
    - 按 visibility 路由回复到合适的 Bridge 连接
    """

    def __init__(
        self,
        conversation_manager: Any,
        port: int = 9999,
        host: str = "127.0.0.1",
    ) -> None:
        self._conversation_manager = conversation_manager
        self._host = host
        self._port = port
        self._connections: dict[str, BridgeConnection] = {}
        self._server: Any = None

        # 可选钩子（由 server.py 组装时注入）
        self.on_customer_message: Callable[[dict], Awaitable[None]] | None = None
        self.on_operator_message: Callable[[dict], Awaitable[None]] | None = None
        self.on_operator_join: Callable[[dict], Awaitable[None]] | None = None
        self.on_operator_command: Callable[[dict, Command], Awaitable[None]] | None = None
        self.on_admin_command: Callable[[dict, Command], Awaitable[None]] | None = None
        self.on_customer_connect: Callable[[dict], Awaitable[None]] | None = None

    # ------------------------------------------------------------------ #
    # 静态路由表
    # ------------------------------------------------------------------ #

    @staticmethod
    def compute_visibility_targets(visibility: str) -> set[str]:
        """根据 visibility 决定应该转发给哪些 Bridge 角色端。

        public → customer + operator + admin
        side / system → operator + admin（客户看不到）
        """
        try:
            return set(_VISIBILITY_ROUTING[visibility])
        except KeyError as e:
            raise ValueError(f"unknown visibility: {visibility!r}") from e

    # ------------------------------------------------------------------ #
    # 解析层（纯函数，便于单元测试）
    # ------------------------------------------------------------------ #

    def _parse_register(self, msg: dict) -> BridgeConnection:
        return BridgeConnection(
            bridge_type=msg["bridge_type"],
            instance_id=msg["instance_id"],
            capabilities=list(msg.get("capabilities", [])),
        )

    def _parse_operator_command(self, msg: dict) -> Command:
        cmd = parse_command(msg["command"])
        if cmd is None:
            raise ValueError(f"not a command: {msg['command']!r}")
        return cmd

    def _parse_admin_command(self, msg: dict) -> Command:
        cmd = parse_command(msg["command"])
        if cmd is None:
            raise ValueError(f"not a command: {msg['command']!r}")
        return cmd

    # ------------------------------------------------------------------ #
    # 处理层
    # ------------------------------------------------------------------ #

    def _handle_register(self, msg: dict, websocket: WebSocketServerProtocol | None = None) -> BridgeConnection:
        conn = self._parse_register(msg)
        conn.websocket = websocket
        self._connections[conn.instance_id] = conn
        return conn

    def _handle_customer_connect(self, msg: dict) -> None:
        metadata = dict(msg.get("metadata", {}))
        customer = msg.get("customer")
        if customer is not None:
            metadata["customer"] = customer
        self._conversation_manager.create(
            conversation_id=msg["conversation_id"],
            metadata=metadata,
        )

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #

    def _connections_for_role(self, role: str) -> list[BridgeConnection]:
        return [c for c in self._connections.values() if role in c.capabilities]

    async def send_event(
        self,
        event_type: str,
        data: dict,
        conversation_id: str,
        target_capabilities: set[str] | None = None,
    ) -> None:
        """广播协议级事件到已注册 Bridge 连接。

        target_capabilities 为 None 时广播到所有连接（用于 mode.changed 等全局状态通知）。
        传入角色集合时仅发送到拥有匹配 capability 的连接（用于 sla.breach 等运营事件）。
        """
        payload = json.dumps(
            {
                "type": "event",
                "event_type": event_type,
                "conversation_id": conversation_id,
                "data": data,
            }
        )
        for conn in list(self._connections.values()):
            if conn.websocket is None:
                continue
            if target_capabilities is not None:
                if not (set(conn.capabilities) & target_capabilities):
                    continue
            try:
                await conn.websocket.send(payload)
            except Exception:
                logger.exception("send_event failed: %s", conn.instance_id)

    async def send_reply(
        self,
        conversation_id: str,
        text: str,
        visibility: str,
        message_id: str | None = None,
    ) -> None:
        """按 visibility 广播回复消息到匹配角色的 Bridge 连接。"""
        targets = self.compute_visibility_targets(visibility)
        payload = {
            "type": "reply",
            "conversation_id": conversation_id,
            "text": text,
            "message_id": message_id,
            "visibility": visibility,
        }
        data = json.dumps(payload)
        sent: set[str] = set()
        for role in targets:
            for conn in self._connections_for_role(role):
                if conn.instance_id in sent or conn.websocket is None:
                    continue
                sent.add(conn.instance_id)
                try:
                    await conn.websocket.send(data)
                except Exception:
                    logger.exception("send_reply failed: %s", conn.instance_id)

    async def send_edit(
        self,
        conversation_id: str,
        message_id: str,
        text: str,
    ) -> None:
        """广播编辑消息到所有 Bridge 连接（feishu_bridge 用 message_id 查映射调 update_message）。"""
        payload = json.dumps(
            {
                "type": "edit",
                "conversation_id": conversation_id,
                "message_id": message_id,
                "text": text,
            }
        )
        for conn in list(self._connections.values()):
            if conn.websocket is None:
                continue
            try:
                await conn.websocket.send(payload)
            except Exception:
                logger.exception("send_edit failed: %s", conn.instance_id)

    # ------------------------------------------------------------------ #
    # WebSocket 主循环
    # ------------------------------------------------------------------ #

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        registered: BridgeConnection | None = None
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("bridge sent invalid JSON: %r", raw)
                    continue

                msg_type = msg.get("type")
                if msg_type == "register":
                    registered = self._handle_register(msg, websocket)
                    await websocket.send(
                        json.dumps({"type": "registered", "instance_id": registered.instance_id})
                    )
                elif msg_type == "customer_connect":
                    self._handle_customer_connect(msg)
                    # ack 先发，再触发 on_customer_connect（可能产生 auto-dispatch 事件）
                    await websocket.send(
                        json.dumps({
                            "type": "customer_connected",
                            "conversation_id": msg.get("conversation_id", ""),
                        })
                    )
                    if self.on_customer_connect:
                        await self.on_customer_connect(msg)
                elif msg_type == "operator_join" and self.on_operator_join:
                    await self.on_operator_join(msg)
                elif msg_type == "customer_message" and self.on_customer_message:
                    await self.on_customer_message(msg)
                elif msg_type == "operator_message" and self.on_operator_message:
                    await self.on_operator_message(msg)
                elif msg_type == "operator_command" and self.on_operator_command:
                    await self.on_operator_command(msg, self._parse_operator_command(msg))
                elif msg_type == "admin_command" and self.on_admin_command:
                    await self.on_admin_command(msg, self._parse_admin_command(msg))
                else:
                    logger.debug("unhandled bridge message type: %s", msg_type)
        except websockets.ConnectionClosed:
            pass
        finally:
            if registered is not None:
                self._connections.pop(registered.instance_id, None)

    async def start(self) -> None:
        self._server = await websockets.serve(self._handle_connection, self._host, self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def run_forever(self) -> None:
        await self.start()
        assert self._server is not None
        await asyncio.Future()  # 永不 resolve
