"""Volcengine 语音服务（ASR / TTS）共享的二进制帧协议工具。

帧结构：
  [header 4 bytes] [optional sequence 4 bytes] [payload size 4 bytes] [payload]

Header 4 bytes：
  Byte 0: protocol_version << 4 | header_size      (0x11 = v1, header_size=1)
  Byte 1: message_type << 4 | message_type_specific_flags
  Byte 2: serialization << 4 | compression          (0x10=json no_compress, 0x11=json+gzip)
  Byte 3: reserved

Message types:
  0b0001 (1) = CLIENT_FULL_REQUEST       — 首包，含 JSON 配置
  0b0010 (2) = CLIENT_AUDIO_ONLY_REQUEST — 后续音频包（仅 ASR）
  0b1001 (9) = SERVER_FULL_RESPONSE      — server 普通响应
  0b1011 (11) = SERVER_ACK               — TTS server ack
  0b1111 (15) = SERVER_ERROR_RESPONSE    — server 错误

message_type_specific_flags（最低 4 bit）：
  0b0000 = no flag
  0b0001 = first packet (TTS)
  0b0010 = last packet / end of stream
  0b0011 = cached
  对 ASR audio_only: 设 0b0010 = last，告诉 server 音频结束
"""
from __future__ import annotations

import gzip
import json
import struct
from dataclasses import dataclass

# Protocol constants
PROTOCOL_VERSION = 0x01
HEADER_SIZE = 0x01

# Message types (4-bit)
MSG_TYPE_CLIENT_FULL_REQUEST = 0b0001
MSG_TYPE_CLIENT_AUDIO_REQUEST = 0b0010
MSG_TYPE_SERVER_FULL_RESPONSE = 0b1001
MSG_TYPE_SERVER_ACK = 0b1011
MSG_TYPE_SERVER_ERROR = 0b1111

# Flags (lower 4-bit of byte 1)
FLAG_NONE = 0b0000
FLAG_LAST = 0b0010

# Serialization
SER_NO = 0x00
SER_JSON = 0x10  # 上 nibble = 1 = JSON

# Compression
COMP_NO = 0x00
COMP_GZIP = 0x01


def _build_header(message_type: int, flags: int = FLAG_NONE,
                  serialization: int = SER_JSON, compression: int = COMP_GZIP) -> bytes:
    """Pack the 4-byte protocol header."""
    byte0 = (PROTOCOL_VERSION << 4) | HEADER_SIZE
    byte1 = (message_type << 4) | flags
    byte2 = serialization | compression
    byte3 = 0x00
    return struct.pack(">BBBB", byte0, byte1, byte2, byte3)


def encode_full_request(payload: dict) -> bytes:
    """Encode CLIENT_FULL_REQUEST (首包，JSON config)."""
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    header = _build_header(MSG_TYPE_CLIENT_FULL_REQUEST)
    size = struct.pack(">I", len(body))
    return header + size + body


def encode_audio_request(audio: bytes, *, last: bool = False,
                        compress: bool = True) -> bytes:
    """Encode CLIENT_AUDIO_ONLY_REQUEST (ASR 音频帧)。

    Args:
        audio: raw PCM bytes
        last: True 表示最后一个音频包（end of stream）
        compress: gzip 压缩 audio（高带宽场景关掉省 CPU）
    """
    flags = FLAG_LAST if last else FLAG_NONE
    if compress:
        body = gzip.compress(audio)
        comp = COMP_GZIP
    else:
        body = audio
        comp = COMP_NO
    header = _build_header(
        MSG_TYPE_CLIENT_AUDIO_REQUEST, flags=flags,
        serialization=SER_NO, compression=comp,
    )
    size = struct.pack(">I", len(body))
    return header + size + body


def encode_tts_first(payload: dict) -> bytes:
    """Encode TTS v1 submit 请求。

    Doubao TTS v1 submit 协议就是 [header 4] [size 4] [gzipped body]，
    flag=NO_SEQUENCE（不含 sequence 字段）。双向流式 TTS（seed-tts-2.0）
    才用 POS_SEQUENCE + sequence，当前 synthesize 是一次性 submit 模式。

    Header: 0x11 (proto_v=1|hdr_size=1) 0x10 (msg_type=FULL|flag=NO_SEQ)
            0x11 (ser=JSON|comp=GZIP)   0x00 (reserved)
    """
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    header = _build_header(MSG_TYPE_CLIENT_FULL_REQUEST, flags=FLAG_NONE)
    size = struct.pack(">I", len(body))
    return header + size + body


@dataclass
class ParsedFrame:
    """Server frame after parse."""
    message_type: int
    flags: int
    payload: bytes
    json_data: dict | None  # populated if serialization=JSON
    is_error: bool
    is_last: bool


def parse_frame(raw: bytes) -> ParsedFrame:
    """Parse a server-sent binary frame.

    Frame layout 取决于 flags:
      - NO_SEQUENCE (0b0000)：[hdr 4][size 4][body]
      - POS_SEQUENCE (0b0001)：[hdr 4][sequence 4][size 4][body]
      - NEG_SEQUENCE (0b0010) = last without seq：[hdr 4][size 4][body]
      - NEG_WITH_SEQUENCE (0b0011) = last with seq：[hdr 4][sequence 4][size 4][body]
    ERROR_INFORMATION (msg_type=15) 额外在 hdr 后多个 4-byte error_code 字段。

    我们只关心能否拿到 payload；返回 ParsedFrame。
    """
    if len(raw) < 4:
        raise ValueError(f"frame too short: {len(raw)} bytes")
    byte0, byte1, byte2, _byte3 = raw[0], raw[1], raw[2], raw[3]
    header_size = byte0 & 0x0F   # in 4-byte units
    message_type = (byte1 >> 4) & 0x0F
    flags = byte1 & 0x0F
    serialization = byte2 & 0xF0
    compression = byte2 & 0x0F

    cursor = header_size * 4
    is_error = message_type == MSG_TYPE_SERVER_ERROR

    if is_error:
        # 4-byte error code follows header
        if len(raw) < cursor + 4:
            raise ValueError("error frame missing error code")
        cursor += 4

    # Presence of sequence field is driven by flag's lowest bit
    # (POS_SEQUENCE=0b0001 / NEG_WITH_SEQUENCE=0b0011).
    has_sequence = (flags & 0b0001) != 0
    if has_sequence:
        if len(raw) < cursor + 4:
            raise ValueError("frame missing sequence")
        cursor += 4

    if len(raw) < cursor + 4:
        raise ValueError("frame missing payload size")
    payload_size = struct.unpack(">I", raw[cursor:cursor+4])[0]
    cursor += 4
    payload = raw[cursor:cursor + payload_size]

    if compression == COMP_GZIP and payload:
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass  # 留原样供调用方处理

    json_data = None
    if serialization == SER_JSON and payload:
        try:
            json_data = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception:
            pass

    return ParsedFrame(
        message_type=message_type,
        flags=flags,
        payload=payload,
        json_data=json_data,
        is_error=is_error,
        is_last=bool(flags & FLAG_LAST),
    )
