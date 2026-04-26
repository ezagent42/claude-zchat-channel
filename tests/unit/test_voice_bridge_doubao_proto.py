"""voice_bridge._doubao_proto — 帧编解码 round-trip + 关键事件 ID 测试。"""
from __future__ import annotations

import gzip
import json

import pytest

from voice_bridge import _doubao_proto as p


def test_event_id_constants_match_volcengine_spec():
    """护栏：事件 ID 是 vendor wire 协议，不能漂。"""
    assert p.EVENT_START_CONNECTION == 1
    assert p.EVENT_FINISH_CONNECTION == 2
    assert p.EVENT_START_SESSION == 100
    assert p.EVENT_FINISH_SESSION == 102
    assert p.EVENT_TASK_REQUEST == 200
    assert p.EVENT_SAY_HELLO == 300
    assert p.EVENT_CONNECTION_STARTED == 50
    assert p.EVENT_SESSION_STARTED == 150
    assert p.EVENT_TTS_RESPONSE == 352
    assert p.EVENT_ASR_RESPONSE == 451
    assert p.EVENT_ASR_ENDED == 459


def test_build_start_connection_frame_no_session_id():
    """connection-level event 不带 session_id 字段。"""
    frame = p.build_client_frame(p.EVENT_START_CONNECTION, payload={})
    # header 4 bytes + event 4 bytes + size 4 bytes + body
    assert len(frame) >= 12
    assert frame[1] >> 4 == p.CLIENT_FULL_REQUEST
    # event id at offset 4
    event_id = int.from_bytes(frame[4:8], "big")
    assert event_id == p.EVENT_START_CONNECTION


def test_build_start_session_frame_includes_session_id():
    sid = "abc123"
    frame = p.build_client_frame(
        p.EVENT_START_SESSION, session_id=sid, payload={"foo": "bar"}
    )
    # header(4) + event(4) + sid_len(4) + sid(N) + payload_size(4) + payload
    cursor = 4
    event_id = int.from_bytes(frame[cursor:cursor + 4], "big")
    assert event_id == p.EVENT_START_SESSION
    cursor += 4
    sid_len = int.from_bytes(frame[cursor:cursor + 4], "big")
    assert sid_len == len(sid.encode())
    cursor += 4
    assert frame[cursor:cursor + sid_len].decode() == sid


def test_build_audio_frame_uses_no_serialization():
    pcm = b"\x00\x01" * 100
    frame = p.build_client_frame(
        p.EVENT_TASK_REQUEST, session_id="s", payload=pcm, is_audio=True,
    )
    # byte[1] msg_type bits should be CLIENT_AUDIO_ONLY_REQUEST
    assert frame[1] >> 4 == p.CLIENT_AUDIO_ONLY_REQUEST
    # serialization (high nibble of byte[2]) should be NO_SERIALIZATION
    assert frame[2] >> 4 == p.NO_SERIALIZATION


def test_parse_server_frame_full_response_with_event():
    """构造一个 server 帧，roundtrip 解析。"""
    payload = {"hello": "world"}
    body = gzip.compress(json.dumps(payload).encode())
    hdr = bytearray(4)
    hdr[0] = (p.PROTOCOL_VERSION << 4) | p.DEFAULT_HEADER_SIZE
    hdr[1] = (p.SERVER_FULL_RESPONSE << 4) | p.MSG_WITH_EVENT
    hdr[2] = (p.JSON_SERIAL << 4) | p.GZIP_COMPRESSION
    hdr[3] = 0
    event_field = (50).to_bytes(4, "big")  # CONNECTION_STARTED
    sid_len = (0).to_bytes(4, "big")  # no session id
    size_field = len(body).to_bytes(4, "big")
    frame = bytes(hdr) + event_field + sid_len + size_field + body

    parsed = p.parse_server_frame(frame)
    assert parsed["message_type"] == "SERVER_FULL_RESPONSE"
    assert parsed["event"] == 50
    assert parsed["payload_msg"] == payload


def test_parse_server_frame_with_session_id():
    sid = "session-xyz"
    payload = {"k": 1}
    body = gzip.compress(json.dumps(payload).encode())
    hdr = bytearray(4)
    hdr[0] = (p.PROTOCOL_VERSION << 4) | p.DEFAULT_HEADER_SIZE
    hdr[1] = (p.SERVER_FULL_RESPONSE << 4) | p.MSG_WITH_EVENT
    hdr[2] = (p.JSON_SERIAL << 4) | p.GZIP_COMPRESSION
    hdr[3] = 0
    event_field = p.EVENT_SESSION_STARTED.to_bytes(4, "big")
    sid_bytes = sid.encode()
    sid_len = len(sid_bytes).to_bytes(4, "big")
    size_field = len(body).to_bytes(4, "big")
    frame = bytes(hdr) + event_field + sid_len + sid_bytes + size_field + body

    parsed = p.parse_server_frame(frame)
    assert parsed["session_id"] == sid
    assert parsed["event"] == p.EVENT_SESSION_STARTED


def test_parse_server_error_frame():
    payload = {"error": "auth failed"}
    body = gzip.compress(json.dumps(payload).encode())
    hdr = bytearray(4)
    hdr[0] = (p.PROTOCOL_VERSION << 4) | p.DEFAULT_HEADER_SIZE
    hdr[1] = (p.SERVER_ERROR_RESPONSE << 4) | 0  # no MSG_WITH_EVENT
    hdr[2] = (p.JSON_SERIAL << 4) | p.GZIP_COMPRESSION
    hdr[3] = 0
    code_field = (40000045).to_bytes(4, "big")
    size_field = len(body).to_bytes(4, "big")
    frame = bytes(hdr) + code_field + size_field + body

    parsed = p.parse_server_frame(frame)
    assert parsed["message_type"] == "SERVER_ERROR"
    assert parsed["code"] == 40000045
    assert parsed["payload_msg"] == payload


def test_parse_audio_frame_returns_raw_bytes():
    """TTS audio 帧 payload 是 PCM bytes，不应被 JSON 解码。"""
    pcm = b"\x00\x10" * 1000
    body = gzip.compress(pcm)
    hdr = bytearray(4)
    hdr[0] = (p.PROTOCOL_VERSION << 4) | p.DEFAULT_HEADER_SIZE
    hdr[1] = (p.SERVER_FULL_RESPONSE << 4) | p.MSG_WITH_EVENT
    hdr[2] = (p.NO_SERIALIZATION << 4) | p.GZIP_COMPRESSION  # no JSON
    hdr[3] = 0
    event_field = p.EVENT_TTS_RESPONSE.to_bytes(4, "big")
    sid_len = (0).to_bytes(4, "big")
    size_field = len(body).to_bytes(4, "big")
    frame = bytes(hdr) + event_field + sid_len + size_field + body

    parsed = p.parse_server_frame(frame)
    assert parsed["event"] == p.EVENT_TTS_RESPONSE
    assert parsed["payload_msg"] == pcm  # raw bytes


def test_parse_short_frame_returns_empty():
    assert p.parse_server_frame(b"") == {}
    assert p.parse_server_frame(b"\x00\x00\x00") == {}


def test_roundtrip_text_payload():
    """build → parse round-trip for text payload."""
    payload = {"action": "test", "value": 42}
    frame = p.build_client_frame(
        p.EVENT_START_SESSION, session_id="s1", payload=payload,
    )
    # client frames don't have a parser (they go to server) but we can
    # decode the header + payload manually
    hdr_msg_type = frame[1] >> 4
    assert hdr_msg_type == p.CLIENT_FULL_REQUEST
    cursor = 4
    event = int.from_bytes(frame[cursor:cursor + 4], "big")
    cursor += 4
    assert event == p.EVENT_START_SESSION
    sid_len = int.from_bytes(frame[cursor:cursor + 4], "big")
    cursor += 4
    assert frame[cursor:cursor + sid_len].decode() == "s1"
    cursor += sid_len
    payload_size = int.from_bytes(frame[cursor:cursor + 4], "big")
    cursor += 4
    body = gzip.decompress(frame[cursor:cursor + payload_size])
    assert json.loads(body) == payload
