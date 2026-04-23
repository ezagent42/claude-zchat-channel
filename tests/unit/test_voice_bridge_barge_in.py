"""Phase 5 barge-in — session mute + speaker drain + CS event notify."""
from __future__ import annotations

import asyncio

import pytest
from zchat_protocol import ws_messages

from voice_bridge.bridge import VoiceBridge
from voice_bridge.config import VoiceBridgeConfig
from voice_bridge.session import VoiceSession


# ---- session-level ----

@pytest.mark.asyncio
async def test_push_speaker_noop_when_muted():
    s = VoiceSession.new(channel="c", customer="u")
    s.speaker_muted = True
    await s.push_speaker(b"some audio")
    assert s.speaker_queue.empty()


@pytest.mark.asyncio
async def test_push_speaker_resumes_after_unmute():
    s = VoiceSession.new(channel="c", customer="u")
    s.speaker_muted = True
    await s.push_speaker(b"muted")
    s.speaker_muted = False
    await s.push_speaker(b"audible")
    item = await s.speaker_queue.get()
    assert item == b"audible"


def test_drain_speaker_empties_queue():
    s = VoiceSession.new(channel="c", customer="u")
    s.speaker_queue.put_nowait(b"a")
    s.speaker_queue.put_nowait(b"b")
    s.speaker_queue.put_nowait(b"c")
    dropped = s.drain_speaker()
    assert dropped == 3
    assert s.speaker_queue.empty()


def test_drain_speaker_empty_returns_zero():
    s = VoiceSession.new(channel="c", customer="u")
    assert s.drain_speaker() == 0


# ---- bridge-level ----

class _Fake:
    def __init__(self):
        self.sent = []
        self.on_message = None
    async def send(self, msg): self.sent.append(msg)
    @property
    def connected(self): return True


def _bridge_with_fake() -> tuple[VoiceBridge, _Fake]:
    cfg = VoiceBridgeConfig(asr_engine="stub", tts_engine="stub")
    b = VoiceBridge(cfg)
    f = _Fake()
    b._cs_client = f  # type: ignore
    return b, f


@pytest.mark.asyncio
async def test_handle_barge_in_mutes_drains_and_emits_event():
    bridge, fake = _bridge_with_fake()
    session = await bridge.register_session("c1", "alice")
    # Pre-fill speaker queue (agent was mid-reply)
    for _ in range(5):
        await session.push_speaker(b"buffered")
    assert session.speaker_queue.qsize() == 5

    await bridge.handle_barge_in(session)

    assert session.speaker_muted is True
    assert session.speaker_queue.empty()  # drained
    # event emitted to CS
    events = [m for m in fake.sent if m["type"] == "event"]
    assert len(events) == 1
    e = events[0]
    assert e["channel"] == "c1"
    assert e["event"] == "user_barge_in"
    assert e["data"]["session_id"] == session.id
    assert e["data"]["customer"] == "alice"
    assert e["data"]["source"] == "voice-alice"


@pytest.mark.asyncio
async def test_handle_barge_in_on_closed_session_is_noop():
    bridge, fake = _bridge_with_fake()
    session = await bridge.register_session("c", "u")
    session.close()
    await bridge.handle_barge_in(session)
    # no event sent
    assert len([m for m in fake.sent if m["type"] == "event"]) == 0


@pytest.mark.asyncio
async def test_handle_speech_end_unmutes():
    bridge, _ = _bridge_with_fake()
    session = await bridge.register_session("c", "u")
    session.speaker_muted = True
    await bridge.handle_speech_end(session)
    assert session.speaker_muted is False


@pytest.mark.asyncio
async def test_handle_speech_end_on_closed_session_is_noop():
    bridge, _ = _bridge_with_fake()
    session = await bridge.register_session("c", "u")
    session.close()
    session.speaker_muted = True
    await bridge.handle_speech_end(session)
    # closed session stays as-is (won't be speaking again anyway)
    assert session.speaker_muted is True


@pytest.mark.asyncio
async def test_barge_in_mid_fanout_this_session_silenced_others_continue():
    """同 channel 多 session：一个客户开口 → 只该客户 session 停；其他人继续听."""
    cfg = VoiceBridgeConfig(
        asr_engine="stub", tts_engine="stub",
        tts_config={"bytes_per_char": 50, "chunk_size": 100},
    )
    bridge = VoiceBridge(cfg)
    bridge._cs_client = _Fake()  # type: ignore
    s1 = await bridge.register_session("room", "alice")
    s2 = await bridge.register_session("room", "bob")

    # s1 barges in
    await bridge.handle_barge_in(s1)
    assert s1.speaker_muted

    # Simulate agent reply fanout (stub TTS 每字符 50 bytes × 5 字符 = 250)
    await bridge._on_cs_message(ws_messages.build_message(
        channel="room", source="agent", content="__msg:uid:hello",
    ))
    await asyncio.sleep(0.1)

    # s1 got nothing (muted)
    assert s1.speaker_queue.empty()
    # s2 got full TTS
    total_s2 = 0
    while not s2.speaker_queue.empty():
        total_s2 += len(s2.speaker_queue.get_nowait())
    assert total_s2 >= 250


@pytest.mark.asyncio
async def test_barge_in_then_speech_end_resumes_tts():
    bridge, _ = _bridge_with_fake()
    session = await bridge.register_session("c", "alice")

    await bridge.handle_barge_in(session)
    assert session.speaker_muted

    # Simulate agent still replying while muted — nothing lands
    await bridge._on_cs_message(ws_messages.build_message(
        channel="c", source="agent", content="__msg:x:ignored",
    ))
    await asyncio.sleep(0.05)
    assert session.speaker_queue.empty()

    # Customer stops talking; agent now replies again
    await bridge.handle_speech_end(session)
    await bridge._on_cs_message(ws_messages.build_message(
        channel="c", source="agent", content="__msg:y:second answer",
    ))
    await asyncio.sleep(0.1)
    received = b""
    while not session.speaker_queue.empty():
        received += session.speaker_queue.get_nowait()
    assert len(received) > 0


@pytest.mark.asyncio
async def test_barge_in_without_cs_client_still_mutes():
    """No CS connection → barge_in should still mute+drain locally (no event send)."""
    cfg = VoiceBridgeConfig(asr_engine="stub", tts_engine="stub")
    bridge = VoiceBridge(cfg)
    # bridge._cs_client is None
    session = await bridge.register_session("c", "u")
    await session.push_speaker(b"x")
    await bridge.handle_barge_in(session)
    assert session.speaker_muted
    assert session.speaker_queue.empty()
