"""Volcengine Doubao realtime/dialogue TTS engine.

Resource: volc.speech.dialog (Doubao realtime dialog)
URL: wss://openspeech.bytedance.com/api/v3/realtime/dialogue

借 Doubao 端到端 dialog 接口的 TTS 通道（say_hello 事件）。每次 synthesize()
新建一条 WS（Doubao say_hello 在同一连接上只触发一次）。

config:
    app_id, access_token   必填
    voice_type / speaker   可选 — TTS voice，default "zh_female_vv_jupiter_bigtts"
    sample_rate            可选 — TTS 输出采样率，default 24000
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from voice_bridge._doubao_proto import EVENT_TTS_ENDED, EVENT_TTS_RESPONSE
from voice_bridge.doubao_client import DoubaoClient
from voice_bridge.tts.base import TTSChunk

log = logging.getLogger(__name__)


class VolcengineTTS:
    """Doubao realtime/dialogue 流式 TTS。

    每次 synthesize() 建一条 WS 连接（say_hello 事件单连接限一次）；
    一段合成结束自动 close。
    """

    def __init__(self, config: dict) -> None:
        if not config.get("app_id") or not config.get("access_token"):
            raise ValueError(
                "VolcengineTTS requires config.app_id and config.access_token"
            )
        self._config = dict(config)
        # default 24kHz for Doubao TTS（与 _build_session_config 默认一致）
        self._sample_rate = int(config.get("sample_rate") or 24000)
        # 把 sample_rate 同步进 sample_rate_out（DoubaoClient 用的字段名）
        self._config["sample_rate_out"] = self._sample_rate
        self._opened = False

    @property
    def output_format(self) -> str:
        return "pcm_s16le"

    @property
    def output_sample_rate(self) -> int:
        return self._sample_rate

    async def open(self) -> None:
        self._opened = True

    async def close(self) -> None:
        self._opened = False

    async def synthesize(self, text: str) -> AsyncIterator[TTSChunk]:
        if not self._opened:
            raise RuntimeError("VolcengineTTS not open; call open() first")
        text = (text or "").strip()
        if not text:
            yield TTSChunk(audio=b"", is_final=True)
            return

        client = DoubaoClient(self._config)
        await client.connect()
        try:
            await client.send_say_hello(text)
            log.info("[VolcengineTTS] say_hello: %r", text[:60])
            audio_bytes = 0
            async for frame in client.receive():
                event = frame.get("event")
                payload = frame.get("payload_msg")
                if event == EVENT_TTS_RESPONSE and isinstance(payload, (bytes, bytearray)):
                    audio_bytes += len(payload)
                    yield TTSChunk(audio=bytes(payload), is_final=False)
                elif event == EVENT_TTS_ENDED:
                    log.info("[VolcengineTTS] done (%d bytes audio)", audio_bytes)
                    yield TTSChunk(audio=b"", is_final=True)
                    return
                elif frame.get("message_type") == "SERVER_ERROR":
                    log.error("[VolcengineTTS] server error code=%s msg=%s",
                              frame.get("code"), payload)
                    raise RuntimeError(
                        f"Doubao TTS server error code={frame.get('code')}"
                    )
        finally:
            await client.close()
