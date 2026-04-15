"""CardAwareClient — 为 lark_oapi WSS Client 补上 CARD 帧分发。

lark_oapi 1.5.3 的 Client._handle_data_frame 收到 MessageType.CARD 帧
直接 return，不调用任何 handler 也不写回 Response。
CardAwareClient 覆写该方法，将 CARD 帧分发到注册的 card_handler。
"""

from __future__ import annotations

import http
import json
import logging
import time
from typing import Any, Callable

from lark_oapi.core.json import JSON
from lark_oapi.ws.client import Client
from lark_oapi.ws.const import (
    HEADER_BIZ_RT,
    HEADER_MESSAGE_ID,
    HEADER_SEQ,
    HEADER_SUM,
    HEADER_TRACE_ID,
    HEADER_TYPE,
)
from lark_oapi.ws.enum import MessageType
from lark_oapi.ws.model import Response
from lark_oapi.ws.pb.pbbp2_pb2 import Frame

log = logging.getLogger("feishu-bridge.ws_client")


def _get_header(headers: Any, key: str) -> str:
    """从 protobuf headers 中按 key 取值。"""
    for h in headers:
        if h.key == key:
            return h.value
    return ""


class CardAwareClient(Client):
    """继承 lark.ws.Client，补上 CARD 帧分发。

    用法::

        cli = CardAwareClient(
            app_id, app_secret,
            event_handler=handler,
            card_handler=on_card_action,
            log_level=lark.LogLevel.DEBUG,
        )
        cli.start()
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        event_handler: Any = None,
        card_handler: Callable[[dict], None] | None = None,
        log_level: Any = None,
    ) -> None:
        super().__init__(
            app_id,
            app_secret,
            event_handler=event_handler,
            log_level=log_level,
        )
        self._card_handler = card_handler

    async def _handle_data_frame(self, frame: Frame) -> None:
        """覆写：CARD 帧 → card_handler，其余帧 → super()。"""
        hs = frame.headers
        type_ = _get_header(hs, HEADER_TYPE)

        if type_ != MessageType.CARD.value:
            await super()._handle_data_frame(frame)
            return

        # ---- CARD 帧处理 ----
        msg_id = _get_header(hs, HEADER_MESSAGE_ID)
        trace_id = _get_header(hs, HEADER_TRACE_ID)
        sum_ = _get_header(hs, HEADER_SUM)
        seq = _get_header(hs, HEADER_SEQ)

        pl = frame.payload
        if int(sum_ or "1") > 1:
            pl = self._combine(msg_id, int(sum_), int(seq), pl)
            if pl is None:
                return

        resp = Response(code=http.HTTPStatus.OK)
        try:
            start = int(round(time.time() * 1000))
            payload = json.loads(pl.decode("utf-8"))
            log.debug("CARD frame received: msg_id=%s payload=%s", msg_id, payload)
            if self._card_handler:
                self._card_handler(payload)
            end = int(round(time.time() * 1000))
            header = hs.add()
            header.key = HEADER_BIZ_RT
            header.value = str(end - start)
        except Exception as e:
            log.error(
                "card_handler failed: msg_id=%s trace_id=%s err=%s",
                msg_id,
                trace_id,
                e,
            )
            resp = Response(code=http.HTTPStatus.INTERNAL_SERVER_ERROR)

        frame.payload = JSON.marshal(resp).encode("utf-8")
        await self._write_message(frame.SerializeToString())
