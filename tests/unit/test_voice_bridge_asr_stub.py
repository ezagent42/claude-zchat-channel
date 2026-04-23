"""Stub ASR 单元测试。"""
from __future__ import annotations

from typing import AsyncIterator

import pytest

from voice_bridge.asr.stub import StubASR


async def _audio_source(n_chunks: int) -> AsyncIterator[bytes]:
    for i in range(n_chunks):
        yield b"\x00\x01" * 160  # 10ms @ 16kHz s16 mono


@pytest.mark.asyncio
async def test_stream_without_open_raises():
    asr = StubASR()
    with pytest.raises(RuntimeError, match="not open"):
        async for _ in asr.stream(_audio_source(5)):
            pass


@pytest.mark.asyncio
async def test_stream_emits_final_after_chunks():
    """默认 transcripts 单条 final，按 chunks_per_emit 速率 emit。"""
    asr = StubASR(transcripts=[("hello", True)], chunks_per_emit=3)
    await asr.open()
    results = []
    async for r in asr.stream(_audio_source(10)):
        results.append(r)
    assert len(results) == 1
    assert results[0].text == "hello"
    assert results[0].is_final is True


@pytest.mark.asyncio
async def test_stream_emits_interim_then_final():
    asr = StubASR(
        transcripts=[("hel", False), ("hello", True)],
        chunks_per_emit=2,
    )
    await asr.open()
    results = []
    async for r in asr.stream(_audio_source(10)):
        results.append(r)
    assert len(results) == 2
    assert results[0].text == "hel"
    assert results[0].is_final is False
    assert results[1].text == "hello"
    assert results[1].is_final is True


@pytest.mark.asyncio
async def test_stream_drains_remaining_transcripts_at_end_of_stream():
    """输入 audio 结束后仍有未 emit 的 transcripts，应在 stream end 时强制 final emit。"""
    asr = StubASR(
        transcripts=[("a", True), ("b", True)],
        chunks_per_emit=100,  # 远超 audio chunks
    )
    await asr.open()
    results = [r async for r in asr.stream(_audio_source(3))]
    assert len(results) == 2
    assert all(r.is_final for r in results)


@pytest.mark.asyncio
async def test_reopen_after_close_works():
    asr = StubASR()
    await asr.open()
    await asr.close()
    await asr.open()  # should succeed (reset _closed)
    results = [r async for r in asr.stream(_audio_source(10))]
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_empty_chunks_ignored():
    asr = StubASR(transcripts=[("x", True)], chunks_per_emit=2)
    await asr.open()
    async def src():
        yield b""
        yield b"\x00" * 100
        yield b""
        yield b"\x00" * 100
    results = [r async for r in asr.stream(src())]
    # 2 non-empty chunks → 1 emit per chunks_per_emit=2
    assert len(results) == 1
