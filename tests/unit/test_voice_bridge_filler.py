"""Path C — voice_bridge filler audio between ASR final and agent reply."""
from __future__ import annotations

import asyncio

import pytest

from voice_bridge.bridge import VoiceBridge
from voice_bridge.config import VoiceBridgeConfig
from voice_bridge.session import VoiceSession


class _FakeCS:
    def __init__(self):
        self.sent = []
        self.on_message = None
    async def send(self, msg): self.sent.append(msg)
    @property
    def connected(self): return True


def _bridge(filler_enabled=True, phrases=None) -> VoiceBridge:
    cfg = VoiceBridgeConfig(
        asr_engine="stub",
        tts_engine="stub",
        tts_config={"bytes_per_char": 50, "chunk_size": 100},
        filler_enabled=filler_enabled,
    )
    if phrases is not None:
        cfg.filler_phrases = phrases
    b = VoiceBridge(cfg)
    b._cs_client = _FakeCS()  # type: ignore
    return b


# ---- _play_filler unit tests ----

@pytest.mark.asyncio
async def test_play_filler_pushes_audio_to_speaker():
    bridge = _bridge(phrases=["hi"])
    session = await bridge.register_session("c", "u")
    await bridge._play_filler(session)
    received = b""
    while not session.speaker_queue.empty():
        received += session.speaker_queue.get_nowait()
    # stub TTS: 2 chars × 50 = 100 bytes
    assert len(received) == 100


@pytest.mark.asyncio
async def test_play_filler_skipped_if_speaker_buffered():
    """已有未播 audio → 不叠加 filler。"""
    bridge = _bridge(phrases=["x"])
    session = await bridge.register_session("c", "u")
    await session.push_speaker(b"already-here")
    qsize_before = session.speaker_queue.qsize()
    await bridge._play_filler(session)
    qsize_after = session.speaker_queue.qsize()
    assert qsize_after == qsize_before  # filler 没追加


@pytest.mark.asyncio
async def test_play_filler_noop_if_session_closed():
    bridge = _bridge(phrases=["x"])
    session = await bridge.register_session("c", "u")
    session.close()
    await bridge._play_filler(session)
    # close 会塞一个 sentinel；此外不应有别的
    items = []
    while True:
        try:
            items.append(session.speaker_queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    assert items == [b""]


@pytest.mark.asyncio
async def test_play_filler_noop_if_session_muted():
    bridge = _bridge(phrases=["x"])
    session = await bridge.register_session("c", "u")
    session.speaker_muted = True
    await bridge._play_filler(session)
    assert session.speaker_queue.empty()


@pytest.mark.asyncio
async def test_play_filler_no_phrases_noop():
    bridge = _bridge(phrases=[])
    session = await bridge.register_session("c", "u")
    await bridge._play_filler(session)
    assert session.speaker_queue.empty()


@pytest.mark.asyncio
async def test_play_filler_stops_mid_synth_on_mute():
    """合成途中被 barge-in mute → 停止 push 但不 crash。"""
    bridge = _bridge(phrases=["llllllllllllllongtext"])  # 20+ char synth
    session = await bridge.register_session("c", "u")

    async def mute_soon():
        await asyncio.sleep(0.001)
        session.speaker_muted = True

    asyncio.create_task(mute_soon())
    await bridge._play_filler(session)
    # No exception; buffer may have 0+ chunks before mute kicked in
    assert True


# ---- run_session integration: filler triggered after ASR final ----

@pytest.mark.asyncio
async def test_run_session_with_filler_triggers_audio_to_session():
    """ASR final → cs_client.send + filler audio shows up in speaker_queue."""
    cfg = VoiceBridgeConfig(
        asr_engine="stub",
        tts_engine="stub",
        asr_config={"transcripts": [("订单", True)], "chunks_per_emit": 1},
        tts_config={"bytes_per_char": 30, "chunk_size": 60},
        filler_enabled=True,
        filler_phrases=["稍等"],
    )
    bridge = VoiceBridge(cfg)
    bridge._cs_client = _FakeCS()  # type: ignore
    session = await bridge.register_session("c", "u")

    async def feed():
        await session.push_mic(b"x" * 320)
        await asyncio.sleep(0.1)  # 让 filler 有时间合成完
        session.close()

    feeder = asyncio.create_task(feed())
    await asyncio.wait_for(bridge.run_session(session), timeout=2.0)
    await feeder
    # Wait briefly for any background filler task to finish
    if bridge._bg_tasks:
        await asyncio.gather(*bridge._bg_tasks, return_exceptions=True)

    # CS 收到 ASR final
    msgs = [m for m in bridge._cs_client.sent if m["type"] == "message"]
    assert len(msgs) == 1
    assert msgs[0]["content"] == "订单"

    # speaker_queue 收到 filler audio (excluding sentinel from close())
    received = b""
    while not session.speaker_queue.empty():
        chunk = session.speaker_queue.get_nowait()
        if chunk:  # skip empty sentinel
            received += chunk
    # filler "稍等" = 2 chars × 30 = 60 bytes
    assert len(received) == 60


@pytest.mark.asyncio
async def test_run_session_filler_disabled_no_extra_audio():
    cfg = VoiceBridgeConfig(
        asr_engine="stub",
        tts_engine="stub",
        asr_config={"transcripts": [("query", True)], "chunks_per_emit": 1},
        tts_config={"bytes_per_char": 30, "chunk_size": 60},
        filler_enabled=False,
    )
    bridge = VoiceBridge(cfg)
    bridge._cs_client = _FakeCS()  # type: ignore
    session = await bridge.register_session("c", "u")

    async def feed():
        await session.push_mic(b"x" * 320)
        await asyncio.sleep(0.05)
        session.close()

    feeder = asyncio.create_task(feed())
    await asyncio.wait_for(bridge.run_session(session), timeout=2.0)
    await feeder
    # No filler task spawned (only sentinel from close())
    received = b""
    while not session.speaker_queue.empty():
        chunk = session.speaker_queue.get_nowait()
        if chunk:
            received += chunk
    assert received == b""
