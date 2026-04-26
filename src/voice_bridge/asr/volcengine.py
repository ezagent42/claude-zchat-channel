"""Volcengine Doubao realtime/dialogue ASR engine.

Resource: volc.speech.dialog (Doubao realtime dialog)
URL: wss://openspeech.bytedance.com/api/v3/realtime/dialogue

借 Doubao 端到端 dialog 接口的 ASR 通道；忽略 LLM 和 TTS 事件（"E2E-adapter"
模式）。一条 WS 持续接收 mic → 在 ASR_RESPONSE 事件流里出 partial/final 文本。

config:
    app_id, access_token   必填
    asr_language          可选 — default zh-CN
    sample_rate_in        可选 — default 16000
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from voice_bridge._doubao_proto import (
    EVENT_ASR_ENDED,
    EVENT_ASR_INFO,
    EVENT_ASR_RESPONSE,
)
from voice_bridge.asr.base import ASRResult
from voice_bridge.doubao_client import DoubaoClient

log = logging.getLogger(__name__)


class VolcengineASR:
    """Doubao realtime/dialogue 流式 ASR client。"""

    def __init__(self, config: dict) -> None:
        self._config = config
        if not config.get("app_id") or not config.get("access_token"):
            raise ValueError(
                "VolcengineASR requires config.app_id and config.access_token"
            )
        self._client: DoubaoClient | None = None
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        self._client = DoubaoClient(self._config)
        await self._client.connect()
        self._opened = True

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        self._opened = False

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        sample_rate: int = 16000,
        encoding: str = "pcm_s16le",
    ) -> AsyncIterator[ASRResult]:
        """Streaming recognition.

        Yields ASRResult; interim results have is_final=False, definite final
        emitted when ASR_ENDED arrives.
        """
        if self._client is None:
            raise RuntimeError("VolcengineASR not opened")

        send_task = asyncio.create_task(self._pump_audio(audio))
        try:
            last_interim = ""
            async for frame in self._client.receive():
                event = frame.get("event")
                payload = frame.get("payload_msg")

                if event == EVENT_ASR_INFO:
                    # speech_started — caller may use to track barge-in；no text yield
                    continue

                if event == EVENT_ASR_RESPONSE and isinstance(payload, dict):
                    results = payload.get("results") or []
                    if not results:
                        continue
                    last = results[-1]
                    text = (last.get("text") or "").strip()
                    if not text:
                        continue
                    is_interim = bool(last.get("is_interim", True))
                    if is_interim:
                        yield ASRResult(text=text, is_final=False)
                        last_interim = text
                    else:
                        # 服务端给的 final 文本：直接 emit
                        yield ASRResult(text=text, is_final=True)
                        last_interim = ""

                elif event == EVENT_ASR_ENDED:
                    # 一段语音结束。如果之前没拿到 final 但有 interim，把 interim 当 final 提交
                    if last_interim:
                        yield ASRResult(text=last_interim, is_final=True)
                        last_interim = ""

                elif frame.get("message_type") == "SERVER_ERROR":
                    log.error("[VolcengineASR] server error code=%s msg=%s",
                              frame.get("code"), payload)
                    break
        finally:
            send_task.cancel()
            try:
                await send_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _pump_audio(self, audio: AsyncIterator[bytes]) -> None:
        if self._client is None:
            return
        try:
            async for chunk in audio:
                if not chunk:
                    continue
                await self._client.send_audio(chunk)
        except Exception as e:
            log.debug("[VolcengineASR] mic pump ended: %s", e)
