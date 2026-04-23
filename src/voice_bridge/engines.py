"""Engine factory — 按 config.asr_engine / tts_engine 构造引擎实例。

真实引擎（whisper_cpp / piper / volcengine / edge_tts）作为**可选**子类，
import 延迟；未装依赖时 engine_name="stub" 始终可用。
"""
from __future__ import annotations

import logging

from voice_bridge.asr.base import ASREngine
from voice_bridge.asr.stub import StubASR
from voice_bridge.tts.base import TTSEngine
from voice_bridge.tts.stub import StubTTS

log = logging.getLogger(__name__)


def build_asr(engine_name: str, config: dict | None = None) -> ASREngine:
    """Factory — 返回实现 ASREngine 协议的实例。"""
    config = config or {}
    if engine_name == "stub":
        return StubASR(
            transcripts=config.get("transcripts"),
            chunks_per_emit=int(config.get("chunks_per_emit", 5)),
        )
    if engine_name == "whisper_cpp":
        # 延迟 import — 真实环境才需要
        raise NotImplementedError(
            "whisper_cpp ASR engine not yet wired; use engine='stub' in Phase 1"
        )
    if engine_name == "volcengine":
        raise NotImplementedError(
            "volcengine ASR engine not yet wired; use engine='stub' in Phase 1"
        )
    raise ValueError(f"Unknown ASR engine: {engine_name!r}")


def build_tts(engine_name: str, config: dict | None = None) -> TTSEngine:
    """Factory — 返回实现 TTSEngine 协议的实例。"""
    config = config or {}
    if engine_name == "stub":
        return StubTTS(
            bytes_per_char=int(config.get("bytes_per_char", 1600)),
            chunk_size=int(config.get("chunk_size", 3200)),
        )
    if engine_name == "piper":
        raise NotImplementedError(
            "piper TTS engine not yet wired; use engine='stub' in Phase 1"
        )
    if engine_name == "edge_tts":
        raise NotImplementedError(
            "edge_tts TTS engine not yet wired; use engine='stub' in Phase 1"
        )
    raise ValueError(f"Unknown TTS engine: {engine_name!r}")
