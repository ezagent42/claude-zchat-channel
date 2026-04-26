"""voice_bridge → channel_server WS 客户端。

与 feishu_bridge/bridge_api_client.py 功能相似，但全 asyncio 实现（voice_bridge
的 WS server 也是 asyncio，共享一个 event loop）。

连接生命周期：
    client = CSClient(cs_ws_url, instance_id="voice-test-voice")
    await client.connect()
    # 注册成功后 loop 在 background 收广播，通过 on_message 回调派发
    client.on_message = lambda msg: ...
    await client.send(build_message(...))
    await client.close()
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Optional

import websockets
from websockets.asyncio.client import ClientConnection
from zchat_protocol import ws_messages

log = logging.getLogger(__name__)


class CSClient:
    """channel_server 的 WS 客户端。

    调用方在 connect 前设置 on_message 回调；收到每条非 REGISTERED 消息即派发。

    Args:
        url: ws://host:port
        instance_id: 在 CS 侧的唯一标识（CS 用它做 _connections key，不可重复）
        bridge_type: "voice"（feishu_bridge 用 "feishu"）
        reconnect_delay: 断开后自动重连等待秒数；0 = 不重连
    """

    def __init__(
        self,
        *,
        url: str,
        instance_id: str,
        bridge_type: str = "voice",
        reconnect_delay: float = 3.0,
    ) -> None:
        self._url = url
        self._instance_id = instance_id
        self._bridge_type = bridge_type
        self._reconnect_delay = reconnect_delay
        self._ws: Optional[ClientConnection] = None
        self._runner_task: Optional[asyncio.Task] = None
        self._running = False
        self._connected_event = asyncio.Event()

        self.on_message: Optional[Callable[[dict], Awaitable[None]]] = None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def connect(self) -> None:
        """启动后台连接任务，等待首次连接成功（含 register）。"""
        if self._runner_task and not self._runner_task.done():
            return
        self._running = True
        self._connected_event.clear()
        self._runner_task = asyncio.create_task(
            self._runner(), name=f"cs-client-{self._instance_id}"
        )
        # 等首次注册成功（≤10s）；失败抛出 TimeoutError 让调用方知道
        await asyncio.wait_for(self._connected_event.wait(), timeout=10)

    async def close(self) -> None:
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._runner_task is not None:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except (asyncio.CancelledError, Exception):
                pass

    async def send(self, msg: dict[str, Any]) -> None:
        """发一个 ws_messages 字典（调用方已经 build 好）。"""
        if self._ws is None:
            log.warning("send while not connected, dropping: type=%s channel=%s",
                        msg.get("type"), msg.get("channel"))
            return
        try:
            await self._ws.send(json.dumps(msg))
        except Exception:
            log.exception("CS send failed: type=%s channel=%s",
                          msg.get("type"), msg.get("channel"))

    # ------------------------------------------------------------------

    async def _runner(self) -> None:
        """连接循环：若 reconnect_delay > 0 且 close 未被调用则自动重连。"""
        while self._running:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self._running:
                    return
                log.warning("CS disconnected (%s), reconnecting in %.1fs",
                            e, self._reconnect_delay)
                if self._reconnect_delay <= 0:
                    return
                await asyncio.sleep(self._reconnect_delay)
            else:
                # clean exit of _connect_once without exception
                if not self._running:
                    return

    async def _connect_once(self) -> None:
        async with websockets.asyncio.client.connect(self._url) as ws:
            self._ws = ws
            # Register
            register_msg = ws_messages.build_register(
                bridge_type=self._bridge_type,
                instance_id=self._instance_id,
                capabilities=["voice"],
            )
            await ws.send(json.dumps(register_msg))
            # Wait for REGISTERED ack (CS returns it before broadcasting)
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            try:
                resp = ws_messages.parse(raw)
            except Exception:
                log.warning("CS first frame not parseable: %s", raw[:200] if raw else raw)
                raise
            if resp.get("type") != "registered":
                log.warning("CS first frame not 'registered': %s", resp.get("type"))
            log.info("CS registered: %s as %s", self._url, self._instance_id)
            self._connected_event.set()

            async for raw in ws:
                try:
                    msg = ws_messages.parse(raw)
                except Exception:
                    log.debug("ignore unparseable CS frame")
                    continue
                if self.on_message is not None:
                    try:
                        await self.on_message(msg)
                    except Exception:
                        log.exception("on_message callback error for type=%s",
                                      msg.get("type"))

        self._ws = None
        # Block new send()s until reconnected
        self._connected_event.clear()
