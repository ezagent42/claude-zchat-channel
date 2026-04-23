"""Phase 4 streaming — __edit 前缀 delta + replace / unknown / trim."""
from __future__ import annotations

import asyncio

import pytest
from zchat_protocol import ws_messages

from voice_bridge.bridge import (
    VoiceBridge,
    _MSG_BUFFER_PER_CHANNEL,
    _parse_edit,
    _parse_msg,
)
from voice_bridge.config import VoiceBridgeConfig


# ---- parse helpers ----

def test_parse_msg():
    assert _parse_msg("__msg:abc-123:hello") == ("abc-123", "hello")
    assert _parse_msg("__msg:x:a:b:c") == ("x", "a:b:c")
    assert _parse_msg("plain") is None
    assert _parse_msg("__edit:x:y") is None


def test_parse_edit():
    assert _parse_edit("__edit:id-1:new text") == ("id-1", "new text")
    assert _parse_edit("__edit:id:a:b") == ("id", "a:b")
    assert _parse_edit("__msg:x:y") is None


# ---- _extract_speakable ----

def _make_bridge() -> VoiceBridge:
    cfg = VoiceBridgeConfig(asr_engine="stub", tts_engine="stub")
    return VoiceBridge(cfg)


def test_extract_msg_populates_buffer_and_returns_text():
    b = _make_bridge()
    out = b._extract_speakable("c1", "__msg:uid-1:您好")
    assert out == "您好"
    assert b._msg_buffer["c1"]["uid-1"] == "您好"


def test_extract_edit_prefix_delta_returns_only_new_text():
    """Streaming scenario: agent 先发 __msg:uid:您好 再 __edit:uid:您好，请问..."""
    b = _make_bridge()
    b._extract_speakable("c1", "__msg:uid:您好")
    # Edit extends → only delta spoken
    out = b._extract_speakable("c1", "__edit:uid:您好，请问您需要什么")
    assert out == "，请问您需要什么"
    assert b._msg_buffer["c1"]["uid"] == "您好，请问您需要什么"


def test_extract_edit_multiple_prefix_appends():
    """Consecutive streaming appends — each edit delivers delta only."""
    b = _make_bridge()
    b._extract_speakable("c", "__msg:u:1")
    assert b._extract_speakable("c", "__edit:u:12") == "2"
    assert b._extract_speakable("c", "__edit:u:123") == "3"
    assert b._extract_speakable("c", "__edit:u:1234") == "4"


def test_extract_edit_replacement_not_prefix_falls_back_to_full():
    """Edit 不是前缀 append（agent 整句改过） → 安全回退念整条."""
    b = _make_bridge()
    b._extract_speakable("c", "__msg:u:原版")
    out = b._extract_speakable("c", "__edit:u:全新内容")
    assert out == "全新内容"
    assert b._msg_buffer["c"]["u"] == "全新内容"


def test_extract_edit_whitespace_only_delta_skipped():
    """仅尾部空白/标点变化 → 不念（避免重复标点）."""
    b = _make_bridge()
    b._extract_speakable("c", "__msg:u:您好")
    out = b._extract_speakable("c", "__edit:u:您好  ")
    assert out == ""


def test_extract_edit_without_prior_msg_falls_back_to_full():
    """voice_bridge 重启后 agent 的 __edit 在 buffer 里找不到 → 念整条."""
    b = _make_bridge()
    out = b._extract_speakable("c", "__edit:ghost-id:这是什么来着")
    assert out == "这是什么来着"
    # 即使没找到，也记下来以便后续继续 streaming
    assert b._msg_buffer["c"]["ghost-id"] == "这是什么来着"


def test_extract_bare_content_no_prefix_returns_as_is():
    """没 __msg/__edit 前缀的裸 text（罕见但防御） → 整条念."""
    b = _make_bridge()
    out = b._extract_speakable("c", "just a plain line")
    assert out == "just a plain line"


def test_buffer_trimmed_to_bound():
    b = _make_bridge()
    for i in range(_MSG_BUFFER_PER_CHANNEL + 20):
        b._extract_speakable("c", f"__msg:id-{i}:msg{i}")
    assert len(b._msg_buffer["c"]) <= _MSG_BUFFER_PER_CHANNEL


def test_buffer_per_channel_isolated():
    b = _make_bridge()
    b._extract_speakable("A", "__msg:u:A-text")
    b._extract_speakable("B", "__msg:u:B-text")   # same id, different channel
    out = b._extract_speakable("A", "__edit:u:A-text-更多")
    assert out == "-更多"
    # B's buffer was not touched
    assert b._msg_buffer["B"]["u"] == "B-text"


# ---- _on_cs_message integration with streaming ----

class _Fake:
    def __init__(self):
        self.sent = []
        self.on_message = None
    async def send(self, msg): self.sent.append(msg)
    @property
    def connected(self): return True


def _bridge_with_fake(cfg_overrides=None) -> tuple[VoiceBridge, _Fake]:
    cfg = VoiceBridgeConfig(
        asr_engine="stub", tts_engine="stub",
        tts_config={"bytes_per_char": 20, "chunk_size": 40},
    )
    if cfg_overrides:
        for k, v in cfg_overrides.items():
            setattr(cfg, k, v)
    b = VoiceBridge(cfg)
    f = _Fake()
    b._cs_client = f  # type: ignore
    return b, f


@pytest.mark.asyncio
async def test_on_cs_streaming_edit_tts_delta_only():
    b, _ = _bridge_with_fake()
    session = await b.register_session("c1", "alice")

    # 1) agent 先发 __msg 整条
    await b._on_cs_message(ws_messages.build_message(
        channel="c1", source="fast", content="__msg:u1:您好",
    ))
    await asyncio.sleep(0.1)
    # Expected TTS: "您好" (2 chars × 20 = 40 bytes)
    received_first = b""
    while not session.speaker_queue.empty():
        received_first += session.speaker_queue.get_nowait()

    # 2) agent 再 __edit 追加（streaming 场景）
    await b._on_cs_message(ws_messages.build_message(
        channel="c1", source="fast", content="__edit:u1:您好，请问您需要什么",
    ))
    await asyncio.sleep(0.1)
    received_second = b""
    while not session.speaker_queue.empty():
        received_second += session.speaker_queue.get_nowait()

    # Delta is "，请问您需要什么" — 8 chars × 20 = 160 bytes
    # vs full replace would be 10 chars × 20 = 200 bytes
    assert len(received_first) > 0
    assert len(received_second) > 0
    # 关键：第二次发的 audio 量应反映 delta 而不是整条
    # delta (8 chars) × 20 = 160; full (10 chars) × 20 = 200
    assert len(received_second) <= 180, \
        f"edit should have spoken only delta, got {len(received_second)} bytes"


@pytest.mark.asyncio
async def test_on_cs_edit_without_prior_speaks_full():
    b, _ = _bridge_with_fake()
    session = await b.register_session("c1", "alice")
    await b._on_cs_message(ws_messages.build_message(
        channel="c1", source="fast", content="__edit:orphan:独立一条",
    ))
    await asyncio.sleep(0.1)
    received = b""
    while not session.speaker_queue.empty():
        received += session.speaker_queue.get_nowait()
    # 4 chars × 20 = 80 bytes
    assert len(received) >= 80
