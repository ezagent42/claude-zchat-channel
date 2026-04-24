"""Volcengine Doubao 流式 TTS。

Endpoint: wss://openspeech.bytedance.com/api/v1/tts/ws_binary
Auth: Authorization: Bearer; <access_token>  （注意 ; 分号）

凭证 + 参数：
  config["app_id"]        必填
  config["access_token"]  必填
  config["cluster"]       可选 — 默认 "volcano_tts"
  config["voice_type"]    可选 — 默认 "BV700_streaming"（通用女声，bilingual）
  config["language"]      可选 — 默认 "cn"（cn / en / mix 等）
  config["sample_rate"]   可选 — 默认 16000
  config["uid"]           可选

支持的 voice_type 示例（截至 2026）：
  - BV700_streaming                       通用女声流式（推荐 bilingual）
  - BV001_streaming                       通用女声
  - zh_female_qingxin_emo_v2_mars_bigtts  豆包清新女声 v2
  - zh_male_xiaoming_emo_v2_mars_bigtts   豆包小明 v2
  - en_female_skye_mars_bigtts            英文女声
完整列表见 Volcengine TTS console。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator

import websockets.asyncio.client
from voice_bridge.tts.base import TTSChunk
from voice_bridge import _volc_proto as proto

log = logging.getLogger(__name__)

_ENDPOINT = "wss://openspeech.bytedance.com/api/v1/tts/ws_binary"
_DEFAULT_CLUSTER = "volcano_tts"
_DEFAULT_VOICE = "BV700_streaming"


class VolcengineTTS:
    """Doubao 流式 TTS client，输出 PCM s16le 16kHz mono。"""

    def __init__(self, config: dict) -> None:
        self._app_id = str(config.get("app_id", "")).strip()
        self._access_token = str(config.get("access_token", "")).strip()
        if not self._app_id or not self._access_token:
            raise ValueError(
                "VolcengineTTS requires config.app_id and config.access_token"
            )
        self._cluster = config.get("cluster") or _DEFAULT_CLUSTER
        self._voice_type = config.get("voice_type") or _DEFAULT_VOICE
        self._language = config.get("language") or "cn"
        self._sample_rate = int(config.get("sample_rate", 16000))
        self._uid = config.get("uid") or "voice_bridge"
        self._endpoint = config.get("endpoint") or _ENDPOINT
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
        if not text or not text.strip():
            yield TTSChunk(audio=b"", is_final=True)
            return

        headers = {"Authorization": f"Bearer; {self._access_token}"}
        request_payload = self._build_request(text)

        async with websockets.asyncio.client.connect(
            self._endpoint, additional_headers=headers, max_size=8 * 1024 * 1024,
        ) as ws:
            await ws.send(proto.encode_tts_first(request_payload))
            async for raw in ws:
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                try:
                    frame = proto.parse_frame(bytes(raw))
                except Exception as e:
                    log.warning("TTS parse_frame failed: %s", e)
                    continue
                if frame.is_error:
                    log.error("TTS server error: payload=%s", frame.payload[:200])
                    yield TTSChunk(audio=b"", is_final=True)
                    return
                # ACK 帧（message_type=11）payload 为空，跳过
                if frame.message_type == proto.MSG_TYPE_SERVER_ACK:
                    if frame.is_last:
                        yield TTSChunk(audio=b"", is_final=True)
                        return
                    continue
                # 普通响应：payload 是音频字节
                audio = frame.payload or b""
                if audio:
                    yield TTSChunk(audio=audio, is_final=frame.is_last)
                if frame.is_last:
                    return

    def _build_request(self, text: str) -> dict:
        return {
            "app": {
                "appid": self._app_id,
                "token": self._access_token,
                "cluster": self._cluster,
            },
            "user": {"uid": self._uid},
            "audio": {
                "voice_type": self._voice_type,
                "encoding": "pcm",
                "rate": self._sample_rate,
                "language": self._language,
            },
            "request": {
                "reqid": uuid.uuid4().hex,
                "text": text,
                "operation": "submit",
            },
        }
