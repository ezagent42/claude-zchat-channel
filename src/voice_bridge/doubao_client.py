"""Doubao realtime/dialogue 高层 WS 客户端。

一条 WS 同时承载 ASR + TTS + dialog state。AutoService 验证过的 E2E-adapter
模式：借连接复用绕过 Doubao 自带 LLM，只用 ASR/TTS 通道。

config 参数：
    app_id: str          # X-Api-App-ID
    access_token: str    # X-Api-Access-Key
    speaker: str         # TTS voice (e.g. zh_female_vv_jupiter_bigtts)
    asr_language: str    # default zh-CN
    sample_rate_in: int  # mic 采样率，default 16000
    sample_rate_out: int # TTS 输出采样率，default 24000

URL: wss://openspeech.bytedance.com/api/v3/realtime/dialogue
Resource: volc.speech.dialog
App Key: PlgvMymc7f3tQnJ6  (Doubao 公共 product key，非 secret)
"""
from __future__ import annotations

import logging
import uuid
from typing import AsyncGenerator

import websockets

from voice_bridge._doubao_proto import (
    EVENT_FINISH_CONNECTION,
    EVENT_FINISH_SESSION,
    EVENT_SAY_HELLO,
    EVENT_START_CONNECTION,
    EVENT_START_SESSION,
    EVENT_TASK_REQUEST,
    build_client_frame,
    parse_server_frame,
)

log = logging.getLogger(__name__)

DOUBAO_WS_URL = "wss://openspeech.bytedance.com/api/v3/realtime/dialogue"
DOUBAO_RESOURCE_ID = "volc.speech.dialog"
DOUBAO_APP_KEY = "PlgvMymc7f3tQnJ6"  # Doubao 公共 product key（非 secret）


def _build_session_config(
    *,
    speaker: str,
    asr_language: str,
    sample_rate_in: int,
    sample_rate_out: int,
) -> dict:
    """Default StartSession payload (E2E-adapter mode：声明 dialog 字段但不用 LLM)。"""
    return {
        "tts": {
            "audio_config": {
                "format": "pcm_s16le",
                "sample_rate": sample_rate_out,
                "channel": 1,
            },
            "speaker": speaker,
        },
        "asr": {
            "audio_info": {
                "format": "pcm",
                "sample_rate": sample_rate_in,
                "channel": 1,
            },
            "extra": {"end_smooth_window_ms": 1500},
        },
        "dialog": {
            "bot_name": "voice_bridge",
            "extra": {"input_mod": "keep_alive", "recv_timeout": 60},
        },
        "extra": {"model": "1.2.1.1"},
    }


def _build_headers(app_id: str, access_token: str) -> dict:
    if not app_id or not access_token:
        raise ValueError("DoubaoClient requires app_id + access_token")
    return {
        "X-Api-App-ID": app_id,
        "X-Api-Access-Key": access_token,
        "X-Api-Resource-Id": DOUBAO_RESOURCE_ID,
        "X-Api-App-Key": DOUBAO_APP_KEY,
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }


class DoubaoClient:
    """Single-WS Doubao realtime/dialogue client.

    Lifecycle:
        client = DoubaoClient(config)
        await client.connect()                    # WS handshake + StartConnection + StartSession
        await client.send_audio(pcm)              # mic frames
        async for frame in client.receive():      # ASR + TTS events
            ...
        await client.close()
    """

    def __init__(self, config: dict):
        self._app_id = str(config.get("app_id", ""))
        self._access_token = str(config.get("access_token", ""))
        self._speaker = str(config.get("speaker") or config.get("voice_type")
                              or "zh_female_vv_jupiter_bigtts")
        self._asr_language = str(config.get("asr_language") or config.get("language") or "zh-CN")
        self._sample_rate_in = int(config.get("sample_rate_in", 16000))
        self._sample_rate_out = int(config.get("sample_rate_out", 24000))
        self.session_id = str(uuid.uuid4())
        self._ws: websockets.ClientConnection | None = None  # type: ignore[name-defined]

    async def connect(self) -> None:
        """WS handshake + StartConnection + StartSession (raises on failure)."""
        headers = _build_headers(self._app_id, self._access_token)
        self._ws = await websockets.connect(
            DOUBAO_WS_URL,
            additional_headers=headers,
            ping_interval=None,
        )
        log.info("Doubao WS connected (session=%s)", self.session_id[:8])
        # StartConnection → expect ConnectionStarted (event=50)
        await self._ws.send(build_client_frame(EVENT_START_CONNECTION, payload={}))
        await self._wait_event(50, error_events=(51,))  # CONNECTION_STARTED / FAILED
        # StartSession → expect SessionStarted (event=150)
        cfg = _build_session_config(
            speaker=self._speaker,
            asr_language=self._asr_language,
            sample_rate_in=self._sample_rate_in,
            sample_rate_out=self._sample_rate_out,
        )
        await self._ws.send(build_client_frame(
            EVENT_START_SESSION, session_id=self.session_id, payload=cfg,
        ))
        await self._wait_event(150, error_events=(153,))  # SESSION_STARTED / FAILED
        log.info("Doubao session started (session=%s)", self.session_id[:8])

    async def _wait_event(self, target: int, error_events: tuple = ()) -> dict:
        if self._ws is None:
            raise RuntimeError("DoubaoClient not connected")
        async for raw in self._ws:
            if not isinstance(raw, (bytes, bytearray)):
                continue
            frame = parse_server_frame(bytes(raw))
            ev = frame.get("event")
            if ev == target:
                return frame
            if ev in error_events:
                raise RuntimeError(
                    f"Doubao event={ev} payload={frame.get('payload_msg')}"
                )
            if frame.get("message_type") == "SERVER_ERROR":
                raise RuntimeError(
                    f"Doubao server error code={frame.get('code')} payload={frame.get('payload_msg')}"
                )
        raise RuntimeError(f"Doubao stream ended before event {target}")

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("DoubaoClient not connected")
        await self._ws.send(build_client_frame(
            EVENT_TASK_REQUEST,
            session_id=self.session_id,
            payload=pcm_bytes,
            is_audio=True,
        ))

    async def send_say_hello(self, text: str) -> None:
        """Trigger TTS: ask Doubao to speak `text` directly (bypasses LLM).

        Note: SAY_HELLO works once per WS connection. To synthesize again,
        callers should reconnect.
        """
        if self._ws is None:
            raise RuntimeError("DoubaoClient not connected")
        await self._ws.send(build_client_frame(
            EVENT_SAY_HELLO,
            session_id=self.session_id,
            payload={"content": text},
        ))

    async def receive(self) -> AsyncGenerator[dict, None]:
        """Yield parsed server frames until WS closes."""
        if self._ws is None:
            return
        async for raw in self._ws:
            if isinstance(raw, (bytes, bytearray)):
                parsed = parse_server_frame(bytes(raw))
                if parsed:
                    yield parsed

    async def close(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(build_client_frame(
                EVENT_FINISH_SESSION, session_id=self.session_id, payload={},
            ))
        except Exception:
            pass
        try:
            await self._ws.send(build_client_frame(EVENT_FINISH_CONNECTION, payload={}))
        except Exception:
            pass
        try:
            await self._ws.close()
        except Exception:
            pass
        self._ws = None
        log.info("Doubao WS closed (session=%s)", self.session_id[:8])
