"""Stub TTS — 用于测试和 L0 loopback。

行为：为输入文本生成一段"假音频"（长度正比于字符数，内容是静音 PCM）。
不做真合成。用于验证 TTS streaming 协议和 audio 推送管线。
"""
from __future__ import annotations

from typing import AsyncIterator

from voice_bridge.tts.base import TTSChunk


class StubTTS:
    """Deterministic fake TTS for tests.

    为每个字符生成 ``bytes_per_char`` 字节的静音 PCM，切成 ``chunk_size``
    大小的帧 yield。最后一帧 is_final=True。

    Args:
        bytes_per_char: 每字符对应的音频字节数（默认 1600 = 100ms @ 16kHz mono s16）
        chunk_size: 每个 TTSChunk 的字节数（默认 3200 = 200ms）
    """

    def __init__(
        self,
        bytes_per_char: int = 1600,
        chunk_size: int = 3200,
    ) -> None:
        self._bytes_per_char = bytes_per_char
        self._chunk_size = chunk_size
        self._opened = False
        self._closed = False

    @property
    def output_format(self) -> str:
        return "pcm_s16le"

    @property
    def output_sample_rate(self) -> int:
        return 16000

    async def open(self) -> None:
        if self._opened and not self._closed:
            return
        self._opened = True
        self._closed = False

    async def close(self) -> None:
        self._closed = True

    async def synthesize(self, text: str) -> AsyncIterator[TTSChunk]:
        if not self._opened or self._closed:
            raise RuntimeError("StubTTS not open; call open() first")
        total_bytes = max(len(text), 1) * self._bytes_per_char
        silence = bytes(total_bytes)  # all zeros = silent PCM
        offset = 0
        while offset < total_bytes:
            end = min(offset + self._chunk_size, total_bytes)
            yield TTSChunk(audio=silence[offset:end], is_final=(end == total_bytes))
            offset = end
        if total_bytes == 0:
            # Empty text → single empty final chunk
            yield TTSChunk(audio=b"", is_final=True)
