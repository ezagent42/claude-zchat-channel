import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from server import load_instructions
from zchat_protocol.sys_messages import encode_sys_for_irc, decode_sys_from_irc, make_sys_message


def test_sys_message_irc_roundtrip():
    msg = make_sys_message("alice-agent0", "sys.stop_request", {"reason": "test"})
    encoded = encode_sys_for_irc(msg)
    decoded = decode_sys_from_irc(encoded)
    assert decoded["type"] == "sys.stop_request"
    assert decoded["body"]["reason"] == "test"


def test_sys_message_not_user_text():
    assert decode_sys_from_irc("{this is just json-like text}") is None
    assert decode_sys_from_irc("hello world") is None


def test_load_instructions_interpolates_agent_name():
    result = load_instructions("alice-agent0")
    assert "alice-agent0" in result
    assert "$agent_name" not in result


def test_load_instructions_contains_routing_rules():
    result = load_instructions("test-agent")
    assert "/zchat:reply" in result
    assert "/zchat:dm" in result
    assert "/zchat:join" in result
    assert "/zchat:broadcast" in result
    assert "chat_id" in result
    assert "subagent" in result.lower() or "Agent tool" in result


def test_load_instructions_contains_soul_pointer():
    result = load_instructions("test-agent")
    assert "soul.md" in result
