import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from message import detect_mention, clean_mention, chunk_message
from server import CHANNEL_INSTRUCTIONS
from zchat_protocol.sys_messages import encode_sys_for_irc, decode_sys_from_irc, make_sys_message


def test_detect_mention():
    assert detect_mention("@alice-agent0 hello", "alice-agent0") is True
    assert detect_mention("hello @alice-agent0", "alice-agent0") is True
    assert detect_mention("hello everyone", "alice-agent0") is False


def test_clean_mention():
    assert clean_mention("@alice-agent0 hello", "alice-agent0") == "hello"


def test_chunk_message_short():
    assert chunk_message("short") == ["short"]


def test_chunk_message_long():
    text = "a" * 5000
    chunks = chunk_message(text, max_length=400)
    assert len(chunks) > 1
    assert all(len(c) <= 400 for c in chunks)


def test_sys_message_irc_roundtrip():
    msg = make_sys_message("alice-agent0", "sys.stop_request", {"reason": "test"})
    encoded = encode_sys_for_irc(msg)
    decoded = decode_sys_from_irc(encoded)
    assert decoded["type"] == "sys.stop_request"
    assert decoded["body"]["reason"] == "test"


def test_sys_message_not_user_text():
    assert decode_sys_from_irc("{this is just json-like text}") is None
    assert decode_sys_from_irc("hello world") is None


def test_detect_mention_with_dash_separator():
    """Agent names use - separator (IRC compliant)."""
    assert detect_mention("@alice-helper hello", "alice-helper") is True
    assert detect_mention("@alice:helper hello", "alice-helper") is False


def test_channel_instructions_mention_slash_commands():
    """CHANNEL_INSTRUCTIONS should reference available /zchat: commands."""
    assert "/zchat:reply" in CHANNEL_INSTRUCTIONS
    assert "/zchat:join" in CHANNEL_INSTRUCTIONS
    assert "/zchat:dm" in CHANNEL_INSTRUCTIONS
    assert "/zchat:broadcast" in CHANNEL_INSTRUCTIONS
    assert "chat_id" in CHANNEL_INSTRUCTIONS
    assert "reply" in CHANNEL_INSTRUCTIONS
