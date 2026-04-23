"""Phase 2 L1 — CS 广播驱动 TTS + mic → ASR → CS.send 路径。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from zchat_protocol import ws_messages

from voice_bridge.bridge import VoiceBridge, _should_speak, _strip_msg_prefix
from voice_bridge.config import VoiceBridgeConfig


# ---------- helpers ----------

def test_strip_msg_prefix_with_uuid():
    assert _strip_msg_prefix("__msg:abc-123:hello") == "hello"


def test_strip_msg_prefix_preserves_colon_in_text():
    assert _strip_msg_prefix("__msg:xyz:a:b:c") == "a:b:c"


def test_strip_msg_prefix_non_msg_prefix_unchanged():
    assert _strip_msg_prefix("__side:foo") == "__side:foo"
    assert _strip_msg_prefix("plain text") == "plain text"


def test_should_speak_filters_side_sys_empty_not_edit():
    """Phase 4：__edit 由 _extract_speakable 特殊处理（streaming delta），不在顶层过滤。"""
    assert not _should_speak("")
    assert not _should_speak("   ")
    assert not _should_speak("__side:foo")
    assert not _should_speak("__zchat_sys:{\"type\":\"x\"}")
    # __edit no longer filtered here — it's handled in _extract_speakable
    assert _should_speak("__edit:abc")
    assert _should_speak("__msg:uuid:normal")
    assert _should_speak("plain text without prefix")


# ---------- on_cs_message behaviour ----------

class _FakeCSClient:
    """Records all sent messages; no actual WS."""
    def __init__(self):
        self.sent: list[dict] = []
        self.on_message = None
    async def send(self, msg: dict) -> None:
        self.sent.append(msg)
    @property
    def connected(self) -> bool:
        return True


def _make_bridge_with_fake_cs() -> tuple[VoiceBridge, _FakeCSClient]:
    cfg = VoiceBridgeConfig(
        asr_engine="stub",
        tts_engine="stub",
        tts_config={"bytes_per_char": 10, "chunk_size": 50},
    )
    bridge = VoiceBridge(cfg)
    fake = _FakeCSClient()
    bridge._cs_client = fake  # type: ignore[assignment]
    return bridge, fake


@pytest.mark.asyncio
async def test_on_cs_message_ignores_non_message_types():
    bridge, fake = _make_bridge_with_fake_cs()
    session = await bridge.register_session("c1", "zhang")

    # event type should be ignored
    await bridge._on_cs_message(ws_messages.build_event(channel="c1", event="x"))
    await asyncio.sleep(0.05)
    assert session.speaker_queue.empty()


@pytest.mark.asyncio
async def test_on_cs_message_skips_channel_with_no_sessions():
    bridge, _ = _make_bridge_with_fake_cs()
    # no sessions registered at all
    await bridge._on_cs_message(ws_messages.build_message(
        channel="unknown", source="fast-agent", content="hi",
    ))
    # no crash; no TTS fanout. Assert bg_tasks unchanged.
    assert len(bridge._bg_tasks) == 0


@pytest.mark.asyncio
async def test_on_cs_message_skips_own_source_echo():
    """CS broadcast 带回自己发的（source 以 voice- 开头）应忽略。"""
    bridge, _ = _make_bridge_with_fake_cs()
    session = await bridge.register_session("c1", "alice")
    await bridge._on_cs_message(ws_messages.build_message(
        channel="c1", source="voice-alice", content="I just said this",
    ))
    # Give any fanout task a chance to run
    await asyncio.sleep(0.05)
    assert session.speaker_queue.empty()


@pytest.mark.asyncio
async def test_on_cs_message_filters_side_sys_empty():
    """side / sys / 空白 始终过滤；__edit 在 Phase 4 改为 streaming 处理，
    见 test_voice_bridge_streaming.py 的 on_cs_edit 测试."""
    bridge, _ = _make_bridge_with_fake_cs()
    session = await bridge.register_session("c1", "alice")
    for content in [
        "__side:operator message",
        "__zchat_sys:{\"event\":\"resolved\"}",
        "",
    ]:
        await bridge._on_cs_message(ws_messages.build_message(
            channel="c1", source="fast-agent", content=content,
        ))
    await asyncio.sleep(0.05)
    assert session.speaker_queue.empty()


@pytest.mark.asyncio
async def test_on_cs_message_tts_fanout_single_session():
    bridge, _ = _make_bridge_with_fake_cs()
    session = await bridge.register_session("c1", "alice")
    await bridge._on_cs_message(ws_messages.build_message(
        channel="c1", source="linyilun-fast-001",
        content="__msg:uuid-abc:订单已发货",
    ))
    # wait for fanout bg task
    await asyncio.sleep(0.1)
    # stub TTS emits silence; just check we got bytes
    received = b""
    while not session.speaker_queue.empty():
        received += session.speaker_queue.get_nowait()
    assert len(received) > 0


@pytest.mark.asyncio
async def test_on_cs_message_tts_fanout_to_all_sessions_in_channel():
    """N:1 — 同 channel 多 session 都收到同一条 TTS。"""
    bridge, _ = _make_bridge_with_fake_cs()
    s1 = await bridge.register_session("room", "alice")
    s2 = await bridge.register_session("room", "bob")
    other = await bridge.register_session("other", "carol")  # 应该不收

    await bridge._on_cs_message(ws_messages.build_message(
        channel="room", source="agent",
        content="__msg:x:hello everyone",
    ))
    await asyncio.sleep(0.1)

    def drain(s):
        b = b""
        while not s.speaker_queue.empty():
            b += s.speaker_queue.get_nowait()
        return b
    a1 = drain(s1)
    a2 = drain(s2)
    o = drain(other)
    assert len(a1) > 0
    assert len(a2) > 0
    assert o == b""


@pytest.mark.asyncio
async def test_on_cs_message_normalizes_channel_hash_prefix():
    """CS broadcast 的 channel 可能带 '#'，voice_bridge 应 normalize 后匹配。"""
    bridge, _ = _make_bridge_with_fake_cs()
    session = await bridge.register_session("conv-001", "alice")
    await bridge._on_cs_message(ws_messages.build_message(
        channel="#conv-001", source="agent",
        content="__msg:x:with hash prefix",
    ))
    await asyncio.sleep(0.1)
    assert not session.speaker_queue.empty()


# ---------- run_session: mic → ASR → CS.send ----------

@pytest.mark.asyncio
async def test_run_session_sends_build_message_on_final_asr():
    cfg = VoiceBridgeConfig(
        asr_engine="stub",
        tts_engine="stub",
        asr_config={"transcripts": [("partial", False), ("完整一句话", True)],
                    "chunks_per_emit": 2},
    )
    bridge = VoiceBridge(cfg)
    fake = _FakeCSClient()
    bridge._cs_client = fake  # type: ignore
    session = await bridge.register_session("conv-001", "zhang-san")

    async def feed():
        for _ in range(4):
            await session.push_mic(b"x" * 320)
        await asyncio.sleep(0.05)
        session.close()

    feeder = asyncio.create_task(feed())
    await asyncio.wait_for(bridge.run_session(session), timeout=2.0)
    await feeder

    # Should have sent exactly one MESSAGE with the final ASR text
    messages = [m for m in fake.sent if m["type"] == "message"]
    assert len(messages) == 1
    assert messages[0]["channel"] == "conv-001"
    assert messages[0]["source"] == "voice-zhang-san"
    assert messages[0]["content"] == "完整一句话"


@pytest.mark.asyncio
async def test_run_session_skips_empty_asr_final():
    cfg = VoiceBridgeConfig(
        asr_engine="stub",
        tts_engine="stub",
        asr_config={"transcripts": [("", True), ("   ", True), ("real", True)],
                    "chunks_per_emit": 1},
    )
    bridge = VoiceBridge(cfg)
    fake = _FakeCSClient()
    bridge._cs_client = fake  # type: ignore
    session = await bridge.register_session("c", "u")

    async def feed():
        for _ in range(3):
            await session.push_mic(b"y" * 320)
        await asyncio.sleep(0.05)
        session.close()

    asyncio.create_task(feed())
    await asyncio.wait_for(bridge.run_session(session), timeout=2.0)

    messages = [m for m in fake.sent if m["type"] == "message"]
    assert len(messages) == 1
    assert messages[0]["content"] == "real"


@pytest.mark.asyncio
async def test_run_session_requires_cs_connected():
    """没连 CS 就 run_session 应立即 RuntimeError。"""
    cfg = VoiceBridgeConfig(asr_engine="stub", tts_engine="stub")
    bridge = VoiceBridge(cfg)
    session = await bridge.register_session("c", "u")
    with pytest.raises(RuntimeError, match="connect_cs"):
        await bridge.run_session(session)
