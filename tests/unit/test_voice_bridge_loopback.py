"""Phase 1 L0 loopback — end-to-end 测试：session mic → ASR → TTS → speaker。"""
from __future__ import annotations

import asyncio

import pytest

from voice_bridge.bridge import VoiceBridge
from voice_bridge.config import VoiceBridgeConfig


@pytest.mark.asyncio
async def test_loopback_session_drains_mic_and_pushes_speaker():
    """写入 mic_queue → 等一会 → speaker_queue 能读出 stub TTS 的音频。"""
    cfg = VoiceBridgeConfig(
        asr_engine="stub",
        tts_engine="stub",
        asr_config={"transcripts": [("echo-hi", True)], "chunks_per_emit": 3},
        tts_config={"bytes_per_char": 100, "chunk_size": 200},
        loopback=True,
    )
    bridge = VoiceBridge(cfg)
    session = await bridge.register_session(channel="c1", customer="u1")

    async def feed_mic():
        # 3 个 non-empty chunks → stub ASR emit 一次 final "echo-hi"
        for _ in range(3):
            await session.push_mic(b"\x00\x01" * 160)
        await asyncio.sleep(0.05)   # 让 loopback 消化
        session.close()             # 关闭，触发 drain 结束

    loop_task = asyncio.create_task(bridge.run_loopback_session(session))
    feeder = asyncio.create_task(feed_mic())
    try:
        await asyncio.wait_for(loop_task, timeout=2.0)
    finally:
        feeder.cancel()
        try:
            await feeder
        except asyncio.CancelledError:
            pass

    # 应收到 stub TTS 的音频：len("echo-hi")=7 字符 × 100 bytes/char = 700 bytes
    received_audio = b""
    while True:
        try:
            chunk = session.speaker_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        received_audio += chunk
    assert len(received_audio) >= 700
    # silence pattern
    assert received_audio == bytes(len(received_audio))


@pytest.mark.asyncio
async def test_loopback_terminates_when_session_closed_early():
    """session 提前关闭，loopback task 应 gracefully 退出不 hang。"""
    cfg = VoiceBridgeConfig(
        asr_engine="stub",
        tts_engine="stub",
        asr_config={"transcripts": [("a", True), ("b", True)], "chunks_per_emit": 100},
        loopback=True,
    )
    bridge = VoiceBridge(cfg)
    session = await bridge.register_session(channel="c", customer="u")

    async def close_soon():
        await asyncio.sleep(0.05)
        session.close()

    asyncio.create_task(close_soon())
    await asyncio.wait_for(bridge.run_loopback_session(session), timeout=2.0)


@pytest.mark.asyncio
async def test_multiple_sessions_independent_loopback():
    """N 个 session 并发 loopback，互不影响。"""
    cfg = VoiceBridgeConfig(
        asr_engine="stub",
        tts_engine="stub",
        asr_config={"transcripts": [("ok", True)], "chunks_per_emit": 2},
        tts_config={"bytes_per_char": 10, "chunk_size": 100},
        loopback=True,
    )
    bridge = VoiceBridge(cfg)
    s1 = await bridge.register_session(channel="room-1", customer="alice")
    s2 = await bridge.register_session(channel="room-1", customer="bob")
    # Same channel, different sessions — both get independent loopback

    async def feed(s):
        for _ in range(2):
            await s.push_mic(b"xx" * 100)
        await asyncio.sleep(0.05)
        s.close()

    await asyncio.gather(
        bridge.run_loopback_session(s1),
        bridge.run_loopback_session(s2),
        feed(s1),
        feed(s2),
    )

    def collect(sess):
        buf = b""
        while True:
            try:
                buf += sess.speaker_queue.get_nowait()
            except asyncio.QueueEmpty:
                return buf

    a1 = collect(s1)
    a2 = collect(s2)
    assert len(a1) > 0
    assert len(a2) > 0
    # Both got roughly the same amount (stub synthesis of "ok" = 2 chars × 10 = 20 bytes)
    assert abs(len(a1) - len(a2)) < 10


def test_engines_factory_rejects_unknown():
    cfg = VoiceBridgeConfig(asr_engine="does-not-exist")
    bridge = VoiceBridge(cfg)
    import pytest
    with pytest.raises(ValueError, match="Unknown ASR engine"):
        bridge.make_asr()


def test_engines_factory_notes_nyi_for_unwired_engines():
    """whisper_cpp / piper / edge_tts 仍未接；volcengine 已 wired (见 volc_engines tests)."""
    for engine in ("whisper_cpp",):
        cfg = VoiceBridgeConfig(asr_engine=engine)
        bridge = VoiceBridge(cfg)
        import pytest
        with pytest.raises(NotImplementedError):
            bridge.make_asr()
    for engine in ("piper", "edge_tts"):
        cfg = VoiceBridgeConfig(tts_engine=engine)
        bridge = VoiceBridge(cfg)
        import pytest
        with pytest.raises(NotImplementedError):
            bridge.make_tts()
