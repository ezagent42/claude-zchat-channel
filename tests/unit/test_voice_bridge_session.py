"""voice_bridge.session 单元测试。"""
from __future__ import annotations

import asyncio

import pytest

from voice_bridge.session import SessionRegistry, VoiceSession


def test_new_normalizes_channel_hash_prefix():
    s = VoiceSession.new(channel="#conv-001", customer="zhangsan")
    assert s.channel == "conv-001"  # '#' stripped
    assert s.customer == "zhangsan"
    assert s.id  # non-empty
    assert s.started_at > 0
    assert not s.closed


def test_new_without_hash_prefix_unchanged():
    s = VoiceSession.new(channel="plain", customer="x")
    assert s.channel == "plain"


def test_close_marks_and_unblocks():
    s = VoiceSession.new(channel="c", customer="u")
    s.close()
    assert s.closed
    # Sentinel should be readable from queues (put_nowait in close)
    assert s.mic_queue.get_nowait() == b""
    assert s.speaker_queue.get_nowait() == b""


@pytest.mark.asyncio
async def test_push_after_close_is_noop():
    s = VoiceSession.new(channel="c", customer="u")
    s.close()
    await s.push_mic(b"abc")
    await s.push_speaker(b"def")
    # push_mic/push_speaker 不加真实数据（应 early return），
    # 而 close 放了一个 sentinel b""
    assert s.mic_queue.qsize() == 1  # only sentinel
    assert s.speaker_queue.qsize() == 1


@pytest.mark.asyncio
async def test_push_mic_before_close_delivers():
    s = VoiceSession.new(channel="c", customer="u")
    await s.push_mic(b"audio-data")
    item = await asyncio.wait_for(s.mic_queue.get(), timeout=0.5)
    assert item == b"audio-data"


# ---- SessionRegistry ----

def test_registry_add_and_lookup_by_id():
    reg = SessionRegistry()
    s = VoiceSession.new(channel="c", customer="u")
    reg.add(s)
    assert reg.get(s.id) is s
    assert len(reg) == 1


def test_registry_by_channel_single():
    reg = SessionRegistry()
    s = VoiceSession.new(channel="ch-1", customer="u1")
    reg.add(s)
    assert reg.sessions_for_channel("ch-1") == [s]
    assert reg.sessions_for_channel("#ch-1") == [s]  # normalize


def test_registry_by_channel_multiple_sessions_same_channel():
    """N:1 — 同一 channel 多个 session。"""
    reg = SessionRegistry()
    s1 = VoiceSession.new(channel="shared", customer="alice")
    s2 = VoiceSession.new(channel="shared", customer="bob")
    reg.add(s1)
    reg.add(s2)
    found = reg.sessions_for_channel("shared")
    assert len(found) == 2
    assert set(x.customer for x in found) == {"alice", "bob"}


def test_registry_remove_cleans_both_indexes():
    reg = SessionRegistry()
    s = VoiceSession.new(channel="c", customer="u")
    reg.add(s)
    removed = reg.remove(s.id)
    assert removed is s
    assert s.closed  # remove marks closed
    assert reg.get(s.id) is None
    assert reg.sessions_for_channel("c") == []


def test_registry_remove_unknown_returns_none():
    reg = SessionRegistry()
    assert reg.remove("ghost-id") is None


def test_registry_remove_drops_empty_channel_bucket():
    reg = SessionRegistry()
    s = VoiceSession.new(channel="c", customer="u")
    reg.add(s)
    reg.remove(s.id)
    # Internal: once empty, channel bucket itself is removed
    assert "c" not in reg._by_channel


def test_registry_multiple_channels_independent():
    reg = SessionRegistry()
    a = VoiceSession.new(channel="A", customer="u1")
    b = VoiceSession.new(channel="B", customer="u2")
    reg.add(a)
    reg.add(b)
    assert reg.sessions_for_channel("A") == [a]
    assert reg.sessions_for_channel("B") == [b]
    reg.remove(a.id)
    assert reg.sessions_for_channel("A") == []
    assert reg.sessions_for_channel("B") == [b]
