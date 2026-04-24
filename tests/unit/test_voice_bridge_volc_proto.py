"""Volcengine 协议帧编码/解码测试（不连真实服务）。"""
from __future__ import annotations

import gzip
import json
import struct

import pytest

from voice_bridge import _volc_proto as proto


# ---- encode_full_request ----

def test_encode_full_request_round_trip():
    payload = {"user": {"uid": "x"}, "audio": {"format": "pcm"}}
    raw = proto.encode_full_request(payload)
    # First 4 bytes header
    assert raw[0] == 0x11  # protocol_version=1, header_size=1
    assert raw[1] == (proto.MSG_TYPE_CLIENT_FULL_REQUEST << 4) | 0  # type=1, no flag
    assert raw[2] == proto.SER_JSON | proto.COMP_GZIP
    # Bytes 4-7: payload size (big endian)
    body_size = struct.unpack(">I", raw[4:8])[0]
    body = raw[8:]
    assert len(body) == body_size
    decompressed = gzip.decompress(body).decode("utf-8")
    assert json.loads(decompressed) == payload


def test_encode_audio_request_with_compression():
    audio = b"\x00\x01" * 1000
    raw = proto.encode_audio_request(audio, last=False, compress=True)
    assert raw[0] == 0x11
    assert raw[1] == (proto.MSG_TYPE_CLIENT_AUDIO_REQUEST << 4) | 0
    assert raw[2] & 0x0F == proto.COMP_GZIP
    body_size = struct.unpack(">I", raw[4:8])[0]
    body = raw[8:8 + body_size]
    assert gzip.decompress(body) == audio


def test_encode_audio_request_last_flag():
    raw = proto.encode_audio_request(b"x", last=True)
    flags = raw[1] & 0x0F
    assert flags == proto.FLAG_LAST


def test_encode_audio_request_no_compression():
    audio = b"\x42" * 100
    raw = proto.encode_audio_request(audio, last=False, compress=False)
    body_size = struct.unpack(">I", raw[4:8])[0]
    body = raw[8:8 + body_size]
    assert body == audio  # not gzipped
    assert raw[2] & 0x0F == proto.COMP_NO


def test_encode_tts_first_uses_no_sequence_flag():
    """Doubao v1 TTS submit 用 NO_SEQUENCE flag — 没有 sequence 字段。
    （bidirectional seed-tts-2.0 才用 POS_SEQUENCE，不是当前 submit 模式）。"""
    raw = proto.encode_tts_first({"text": "hi"})
    flags = raw[1] & 0x0F
    assert flags == proto.FLAG_NONE  # 0b0000
    # layout: [hdr 4][size 4][body]，无 sequence
    msg_type = (raw[1] >> 4) & 0x0F
    assert msg_type == proto.MSG_TYPE_CLIENT_FULL_REQUEST


# ---- parse_frame ----

def _build_server_frame(message_type: int, payload: bytes,
                        flags: int = 0, serialization: int = proto.SER_JSON,
                        compression: int = proto.COMP_GZIP,
                        is_error: bool = False) -> bytes:
    byte0 = 0x11
    byte1 = (message_type << 4) | flags
    byte2 = serialization | compression
    byte3 = 0x00
    header = struct.pack(">BBBB", byte0, byte1, byte2, byte3)
    if compression == proto.COMP_GZIP:
        body = gzip.compress(payload)
    else:
        body = payload
    if is_error:
        # 4-byte error code prefix (mocked)
        return header + struct.pack(">I", 12345) + struct.pack(">I", len(body)) + body
    return header + struct.pack(">I", len(body)) + body


def test_parse_frame_server_full_response_json():
    payload_json = {"result": {"text": "hello", "utterances": [{"is_final": True}]}}
    raw = _build_server_frame(
        proto.MSG_TYPE_SERVER_FULL_RESPONSE,
        json.dumps(payload_json).encode("utf-8"),
    )
    frame = proto.parse_frame(raw)
    assert frame.message_type == proto.MSG_TYPE_SERVER_FULL_RESPONSE
    assert not frame.is_error
    assert not frame.is_last
    assert frame.json_data == payload_json


def test_parse_frame_last_flag_detected():
    raw = _build_server_frame(
        proto.MSG_TYPE_SERVER_FULL_RESPONSE,
        b'{"x":1}',
        flags=proto.FLAG_LAST,
    )
    frame = proto.parse_frame(raw)
    assert frame.is_last


def test_parse_frame_audio_payload_no_serialization():
    audio = b"\x42" * 50
    raw = _build_server_frame(
        proto.MSG_TYPE_SERVER_FULL_RESPONSE,
        audio, serialization=proto.SER_NO, compression=proto.COMP_NO,
    )
    frame = proto.parse_frame(raw)
    assert frame.payload == audio
    assert frame.json_data is None  # SER_NO


def test_parse_frame_error_response():
    raw = _build_server_frame(
        proto.MSG_TYPE_SERVER_ERROR,
        b'{"error":"invalid token"}',
        is_error=True,
    )
    frame = proto.parse_frame(raw)
    assert frame.is_error
    assert frame.json_data == {"error": "invalid token"}


def test_parse_frame_too_short():
    with pytest.raises(ValueError, match="too short"):
        proto.parse_frame(b"\x11\x10")


def test_parse_frame_missing_payload_size():
    with pytest.raises(ValueError, match="payload size"):
        proto.parse_frame(b"\x11\x10\x10\x00\xff")  # only 1 extra byte
