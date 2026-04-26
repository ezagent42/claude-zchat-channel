"""Doubao realtime/dialogue 二进制协议编解码。

来源：从 AutoService channels/web/voice/protocol.py 移植，AutoService 自己改自
volcengine 官方 realtime_dialog_example/python3.7/protocol.py（含官方 cursor bug
修复）。

帧格式：
  [hdr:4][event:4][optional seq:4][optional sid_len:4 + sid:N][size:4][gzipped payload:N]

Header byte 1: (proto_ver << 4) | hdr_size
Header byte 2: (msg_type << 4) | flags
Header byte 3: (serialization << 4) | compression
Header byte 4: reserved
"""
from __future__ import annotations

import gzip
import json
from typing import Any

PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

# Message types
CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111

# Flags (bitfield)
NO_SEQUENCE = 0b0000
NEG_SEQUENCE = 0b0010
MSG_WITH_EVENT = 0b0100

# Serialization
NO_SERIALIZATION = 0b0000
JSON_SERIAL = 0b0001

# Compression
NO_COMPRESSION = 0b0000
GZIP_COMPRESSION = 0b0001

# Client event IDs
EVENT_START_CONNECTION = 1
EVENT_FINISH_CONNECTION = 2
EVENT_START_SESSION = 100
EVENT_FINISH_SESSION = 102
EVENT_TASK_REQUEST = 200      # audio frames
EVENT_SAY_HELLO = 300         # 直接说指定文本（最稳的 TTS 触发）
EVENT_CHAT_TTS_TEXT = 500     # streaming TTS 文本
EVENT_CHAT_TEXT_QUERY = 501
EVENT_CHAT_RAG_TEXT = 502
EVENT_CLIENT_INTERRUPT = 515  # barge-in

# Server event IDs
EVENT_CONNECTION_STARTED = 50
EVENT_CONNECTION_FAILED = 51
EVENT_CONNECTION_FINISHED = 52
EVENT_SESSION_STARTED = 150
EVENT_SESSION_FINISHED = 152
EVENT_SESSION_FAILED = 153
EVENT_USAGE_RESPONSE = 154
EVENT_TTS_SENTENCE_START = 350
EVENT_TTS_SENTENCE_END = 351
EVENT_TTS_RESPONSE = 352      # audio chunks（payload 是 raw PCM）
EVENT_TTS_ENDED = 359
EVENT_ASR_INFO = 450          # speech_started 提示
EVENT_ASR_RESPONSE = 451      # interim/final 文本
EVENT_ASR_ENDED = 459         # 一段语音结束
EVENT_CHAT_RESPONSE = 550
EVENT_CHAT_ENDED = 559

# Connection-level events: frame 中无 session_id
CONNECTION_EVENTS = {EVENT_START_CONNECTION, EVENT_FINISH_CONNECTION}


def _generate_header(
    message_type: int = CLIENT_FULL_REQUEST,
    serial_method: int = JSON_SERIAL,
    compression_type: int = GZIP_COMPRESSION,
) -> bytearray:
    h = bytearray(4)
    h[0] = (PROTOCOL_VERSION << 4) | DEFAULT_HEADER_SIZE
    h[1] = (message_type << 4) | MSG_WITH_EVENT
    h[2] = (serial_method << 4) | compression_type
    h[3] = 0x00
    return h


def build_client_frame(
    event_id: int,
    session_id: str | None = None,
    payload: Any = None,
    is_audio: bool = False,
) -> bytes:
    """构造 client → server 帧。

    is_audio=True 时 payload 是 raw PCM bytes，serialization=NO；
    否则 payload dict 走 JSON+gzip。
    """
    msg_type = CLIENT_AUDIO_ONLY_REQUEST if is_audio else CLIENT_FULL_REQUEST
    serial = NO_SERIALIZATION if is_audio else JSON_SERIAL

    buf = bytearray(_generate_header(msg_type, serial, GZIP_COMPRESSION))
    buf.extend(event_id.to_bytes(4, "big"))

    if event_id not in CONNECTION_EVENTS and session_id is not None:
        sid_bytes = session_id.encode()
        buf.extend(len(sid_bytes).to_bytes(4, "big"))
        buf.extend(sid_bytes)

    if is_audio and isinstance(payload, (bytes, bytearray)):
        body = gzip.compress(bytes(payload))
    else:
        body = gzip.compress(json.dumps(payload or {}).encode("utf-8"))
    buf.extend(len(body).to_bytes(4, "big"))
    buf.extend(body)
    return bytes(buf)


def parse_server_frame(data: bytes) -> dict:
    """解析 server → client 帧。返回结构：
        {
          "message_type": "SERVER_FULL_RESPONSE" | "SERVER_ACK" | "SERVER_ERROR",
          "event": int (if MSG_WITH_EVENT flag),
          "seq": int (if NEG_SEQUENCE flag),
          "session_id": str (optional),
          "payload_msg": dict | bytes | str,  # JSON 解码后 dict / 原始 bytes
          "payload_size": int,
          "code": int (only for ERROR),
        }

    空 / 短帧 → 返回空 dict。
    """
    if len(data) < 4:
        return {}

    header_size = data[0] & 0x0F
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    compression = data[2] & 0x0F

    cursor = header_size * 4
    result: dict[str, Any] = {}

    if message_type in (SERVER_FULL_RESPONSE, SERVER_ACK):
        result["message_type"] = (
            "SERVER_ACK" if message_type == SERVER_ACK else "SERVER_FULL_RESPONSE"
        )
        if flags & NEG_SEQUENCE:
            result["seq"] = int.from_bytes(data[cursor:cursor + 4], "big")
            cursor += 4
        if flags & MSG_WITH_EVENT:
            result["event"] = int.from_bytes(data[cursor:cursor + 4], "big")
            cursor += 4

        # session id（可能 0 长度 = 无）
        sid_len = int.from_bytes(data[cursor:cursor + 4], "big", signed=True)
        cursor += 4
        if sid_len > 0:
            result["session_id"] = data[cursor:cursor + sid_len].decode("utf-8", errors="replace")
            cursor += sid_len

        payload_size = int.from_bytes(data[cursor:cursor + 4], "big")
        cursor += 4
        body = data[cursor:cursor + payload_size]
        if compression == GZIP_COMPRESSION and payload_size > 0:
            body = gzip.decompress(body)
        if serialization == JSON_SERIAL and payload_size > 0:
            try:
                body = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pass  # 保留原 bytes
        result["payload_msg"] = body
        result["payload_size"] = payload_size

    elif message_type == SERVER_ERROR_RESPONSE:
        result["message_type"] = "SERVER_ERROR"
        result["code"] = int.from_bytes(data[cursor:cursor + 4], "big")
        cursor += 4
        payload_size = int.from_bytes(data[cursor:cursor + 4], "big")
        cursor += 4
        body = data[cursor:cursor + payload_size]
        if compression == GZIP_COMPRESSION and payload_size > 0:
            body = gzip.decompress(body)
        if serialization == JSON_SERIAL and payload_size > 0:
            try:
                body = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pass
        result["payload_msg"] = body
        result["payload_size"] = payload_size

    return result
