"""Stub TTS 单元测试。"""
from __future__ import annotations

import pytest

from voice_bridge.tts.stub import StubTTS


@pytest.mark.asyncio
async def test_synthesize_without_open_raises():
    tts = StubTTS()
    with pytest.raises(RuntimeError, match="not open"):
        async for _ in tts.synthesize("hello"):
            pass


@pytest.mark.asyncio
async def test_synthesize_emits_chunks_and_terminal_final():
    tts = StubTTS(bytes_per_char=1600, chunk_size=3200)
    await tts.open()
    chunks = []
    async for c in tts.synthesize("hello"):  # 5 chars × 1600 = 8000 bytes
        chunks.append(c)
    total_bytes = sum(len(c.audio) for c in chunks)
    assert total_bytes == 5 * 1600
    assert chunks[-1].is_final
    assert not any(c.is_final for c in chunks[:-1])


@pytest.mark.asyncio
async def test_synthesize_audio_is_silence():
    tts = StubTTS()
    await tts.open()
    audio = b""
    async for c in tts.synthesize("abc"):
        audio += c.audio
    assert audio == bytes(len(audio))  # all zero = silence


@pytest.mark.asyncio
async def test_empty_text_emits_single_empty_final_chunk():
    tts = StubTTS()
    await tts.open()
    chunks = [c async for c in tts.synthesize("")]
    # empty text → one char worth of silence (bytes_per_char * 1)
    # (implementation currently: max(len(text),1) = 1, so 1 char worth)
    total = sum(len(c.audio) for c in chunks)
    assert total == tts._bytes_per_char
    assert chunks[-1].is_final


def test_format_and_sample_rate_are_pcm_16k():
    tts = StubTTS()
    assert tts.output_format == "pcm_s16le"
    assert tts.output_sample_rate == 16000


@pytest.mark.asyncio
async def test_chunk_size_boundaries():
    """chunk_size 不整除 total_bytes 时，最后一块应短于 chunk_size 且 is_final。"""
    tts = StubTTS(bytes_per_char=100, chunk_size=64)  # total = 5*100 = 500
    await tts.open()
    chunks = [c async for c in tts.synthesize("hello")]
    # 500/64 = 7.8125, so 7 full + 1 short
    assert sum(len(c.audio) for c in chunks) == 500
    assert all(len(c.audio) <= 64 for c in chunks)
    assert chunks[-1].is_final
    assert len(chunks[-1].audio) < 64  # last one is short
