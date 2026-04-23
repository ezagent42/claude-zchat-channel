"""Stub ASR — 用于单元测试和 L0 loopback 模式。

行为：把输入音频字节数当"时长"，根据累积字节数在某些阈值 emit 预设文本。
不做真识别。仅用于验证 pipeline 贯通和 streaming 协议。
"""
from __future__ import annotations

from typing import AsyncIterator

from voice_bridge.asr.base import ASRResult


class StubASR:
    """Deterministic fake ASR for tests.

    如果构造时传 ``transcripts``：按 audio 帧数驱动，每 N 帧输出一句。
    否则用默认 loopback 行为（单句 final）。

    Args:
        transcripts: list of (text, is_final) 顺序 emit
        chunks_per_emit: 每 N 个音频 chunk emit 一次 ASRResult
    """

    def __init__(
        self,
        transcripts: list[tuple[str, bool]] | None = None,
        chunks_per_emit: int = 5,
    ) -> None:
        self._transcripts = transcripts or [("loopback-text", True)]
        self._chunks_per_emit = max(1, chunks_per_emit)
        self._opened = False
        self._closed = False

    async def open(self) -> None:
        if self._opened and not self._closed:
            return
        self._opened = True
        self._closed = False

    async def close(self) -> None:
        self._closed = True

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        sample_rate: int = 16000,
        encoding: str = "pcm_s16le",
    ) -> AsyncIterator[ASRResult]:
        if not self._opened or self._closed:
            raise RuntimeError("StubASR not open; call open() first")
        transcript_idx = 0
        chunk_count = 0
        async for chunk in audio:
            if not chunk:
                continue
            chunk_count += 1
            if chunk_count % self._chunks_per_emit == 0 and transcript_idx < len(self._transcripts):
                text, is_final = self._transcripts[transcript_idx]
                transcript_idx += 1
                yield ASRResult(text=text, is_final=is_final)
        # End-of-stream: emit remaining with is_final forced
        while transcript_idx < len(self._transcripts):
            text, _ = self._transcripts[transcript_idx]
            transcript_idx += 1
            yield ASRResult(text=text, is_final=True)
