"""Volcengine BigModel 流式 ASR (v3 sauc/bigmodel)。

中英文混合识别 + 流式 partial/final + 自带 VAD。

凭证：
  config["app_id"]        必填 — Volcengine 控制台拿
  config["access_token"]  必填 — 同上
  config["resource_id"]   可选 — 默认 "volc.bigasr.sauc.duration"
  config["model_name"]    可选 — 默认 "bigmodel"
  config["language"]      可选 — 默认 "zh-CN"，bilingual 用 "zh-CN+en-US" 或单独 "en-US"
  config["uid"]           可选 — 用户标识，默认 "voice_bridge"

Endpoint: wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator

import websockets.asyncio.client
from voice_bridge.asr.base import ASRResult
from voice_bridge import _volc_proto as proto

log = logging.getLogger(__name__)

_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
_DEFAULT_RESOURCE_ID = "volc.bigasr.sauc.duration"
_DEFAULT_MODEL = "bigmodel"


class VolcengineASR:
    """v3 BigModel 流式 ASR client。"""

    def __init__(self, config: dict) -> None:
        self._app_id = str(config.get("app_id", "")).strip()
        self._access_token = str(config.get("access_token", "")).strip()
        if not self._app_id or not self._access_token:
            raise ValueError(
                "VolcengineASR requires config.app_id and config.access_token"
            )
        self._resource_id = config.get("resource_id") or _DEFAULT_RESOURCE_ID
        self._model_name = config.get("model_name") or _DEFAULT_MODEL
        self._language = config.get("language") or "zh-CN"
        self._uid = config.get("uid") or "voice_bridge"
        self._endpoint = config.get("endpoint") or _ENDPOINT

        # VAD config
        self._end_window_ms = int(config.get("end_window_ms", 800))
        self._force_speech_ms = int(config.get("force_to_speech_ms", 0))

        self._opened = False

    async def open(self) -> None:
        self._opened = True

    async def close(self) -> None:
        self._opened = False

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        sample_rate: int = 16000,
        encoding: str = "pcm_s16le",
    ) -> AsyncIterator[ASRResult]:
        if not self._opened:
            raise RuntimeError("VolcengineASR not open; call open() first")

        connect_id = uuid.uuid4().hex
        request_id = uuid.uuid4().hex
        headers = {
            "X-Api-App-Key": self._app_id,
            "X-Api-Access-Key": self._access_token,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Connect-Id": connect_id,
            "X-Api-Request-Id": request_id,
        }

        async with websockets.asyncio.client.connect(
            self._endpoint, additional_headers=headers, max_size=8 * 1024 * 1024,
        ) as ws:
            # 1) 首包 — full client request 含 user/audio/request 三段配置
            first = self._build_first_payload(sample_rate=sample_rate)
            await ws.send(proto.encode_full_request(first))

            # 2) 并发：上行音频帧 + 下行 ASR 结果
            send_task = asyncio.create_task(_pump_audio(ws, audio))
            # session 级状态：已经 emit 为 final 的 definite utterance 数
            n_definite_emitted = 0
            try:
                async for raw in ws:
                    if not isinstance(raw, (bytes, bytearray)):
                        continue
                    try:
                        frame = proto.parse_frame(bytes(raw))
                    except Exception as e:
                        log.warning("ASR parse_frame failed: %s", e)
                        continue
                    if frame.is_error:
                        log.error("ASR server error: payload=%s",
                                  frame.payload[:200])
                        return
                    data = frame.json_data or {}
                    result = _locate_result(data)
                    if result is None:
                        if frame.is_last:
                            return
                        continue
                    # Debug: dump raw dict (comment out in prod)
                    # import json as _json
                    # log.debug("ASR raw: %s", _json.dumps(data, ensure_ascii=False))
                    utterances = result.get("utterances") or []
                    # 1) 新出现的 definite utterance — 逐条 emit 为 final
                    definite_uts = [
                        u for u in utterances
                        if isinstance(u, dict) and u.get("definite")
                    ]
                    for ut in definite_uts[n_definite_emitted:]:
                        text = (ut.get("text") or "").strip()
                        if text:
                            yield ASRResult(text=text, is_final=True)
                    n_definite_emitted = len(definite_uts)
                    # 2) 当前 tentative partial（尚未 definite 的那句）
                    tentative = next(
                        (u for u in reversed(utterances)
                         if isinstance(u, dict) and not u.get("definite")),
                        None,
                    )
                    if tentative:
                        ttext = (tentative.get("text") or "").strip()
                        if ttext:
                            yield ASRResult(text=ttext, is_final=False)
                    elif not utterances:
                        # 没有 utterances 时 fallback：顶层 result.text
                        top = (result.get("text") or "").strip()
                        if top:
                            yield ASRResult(text=top, is_final=False)
                    if frame.is_last:
                        return
            finally:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass

    def _build_first_payload(self, *, sample_rate: int) -> dict:
        return {
            "user": {"uid": self._uid},
            "audio": {
                "format": "pcm",
                "rate": sample_rate,
                "bits": 16,
                "channel": 1,
                "codec": "raw",
            },
            "request": {
                "model_name": self._model_name,
                "language": self._language,
                "enable_itn": True,
                "enable_punc": True,
                "result_type": "single",        # 单条 partial→final 流
                "show_utterances": True,        # 含分句信息
                "vad": {
                    "vad_enable": True,
                    "end_window_size": self._end_window_ms,
                    "force_to_speech_time": self._force_speech_ms,
                },
            },
        }


async def _pump_audio(ws, audio: AsyncIterator[bytes]) -> None:
    """从 audio iterator 取 PCM chunks 发到 server。流尾发 last 帧。"""
    sent_any = False
    try:
        async for chunk in audio:
            if not chunk:
                continue
            sent_any = True
            await ws.send(proto.encode_audio_request(chunk, last=False))
    except asyncio.CancelledError:
        return
    finally:
        # 发空 last 帧告诉 server 流结束（即使没有数据也要发一帧，
        # 否则 server 可能一直等待）
        try:
            await ws.send(proto.encode_audio_request(b"", last=True))
        except Exception:
            pass
        if not sent_any:
            log.debug("ASR stream ended without any audio sent")


def _locate_result(data: dict) -> dict | None:
    """在 Volcengine 响应 dict 里定位含 text/utterances 的 result 子段。

    可能的嵌套：
      {"payload_msg": {"result": {...}}}  — v3 某些路径
      {"result": {...}}                   — 主流路径
      {"text": ..., "utterances": ...}    — 平铺变体
    """
    if not data:
        return None
    candidates: list[dict] = []
    payload_msg = data.get("payload_msg") if isinstance(data, dict) else None
    if isinstance(payload_msg, dict):
        if isinstance(payload_msg.get("result"), dict):
            candidates.append(payload_msg["result"])
        candidates.append(payload_msg)
    if isinstance(data.get("result"), dict):
        candidates.append(data["result"])
    candidates.append(data)
    for c in candidates:
        if isinstance(c, dict) and ("text" in c or "utterances" in c):
            return c
    return None


def _extract_result(data: dict) -> ASRResult | None:
    """旧接口：返回单个 ASRResult。当前只用在单测里。

    生产路径：stream() 内部按 utterances[].definite 逐条 emit，见那里。
    此函数返回的 is_final 由"最后一个 utterance 是否 definite"决定，供
    测试单步状态校验；不再代表运行时语义。
    """
    result = _locate_result(data)
    if result is None:
        return None
    text = (result.get("text") or "").strip()
    utterances = result.get("utterances") or []
    is_final = False
    if utterances and isinstance(utterances, list):
        last = utterances[-1] if isinstance(utterances[-1], dict) else {}
        is_final = bool(last.get("definite") or last.get("is_final"))
    if not text and not utterances:
        return None
    return ASRResult(text=text, is_final=is_final)
