"""Unit 测试 transport/irc_transport.py（完全 mock IRC 连接）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from transport.irc_transport import IRCTransport


@pytest.fixture
def transport() -> IRCTransport:
    return IRCTransport(server="127.0.0.1", port=6667, nick="test-agent")


def test_init_defaults(transport: IRCTransport) -> None:
    assert transport.nick == "test-agent"
    assert transport.server == "127.0.0.1"
    assert transport.port == 6667
    assert transport.joined_channels == set()
    assert transport.tls is False
    assert transport.auth_token == ""


def test_conv_channel_name(transport: IRCTransport) -> None:
    assert transport.conv_channel_name("feishu_oc_abc") == "#conv-feishu_oc_abc"


def test_extract_conv_id_valid(transport: IRCTransport) -> None:
    assert transport.extract_conv_id("#conv-feishu_oc_abc") == "feishu_oc_abc"


def test_extract_conv_id_invalid(transport: IRCTransport) -> None:
    assert transport.extract_conv_id("#admin") is None


def test_extract_conv_id_no_hash(transport: IRCTransport) -> None:
    assert transport.extract_conv_id("feishu_oc_abc") is None


def test_sys_stop_request_reply(transport: IRCTransport) -> None:
    """sys.stop_request 应通过 PRIVMSG 返回 sys.stop_confirmed。"""
    conn = MagicMock()
    msg = {"id": "xyz", "type": "sys.stop_request", "body": {}}

    transport.handle_sys_message(msg, sender_nick="alice", connection=conn)

    assert conn.privmsg.call_count == 1
    target, payload = conn.privmsg.call_args.args
    assert target == "alice"
    assert "sys.stop_confirmed" in payload


def test_sys_join_request_joins_channel(transport: IRCTransport) -> None:
    """sys.join_request 应触发 conn.join() 并记入 joined_channels。"""
    conn = MagicMock()
    msg = {
        "id": "xyz",
        "type": "sys.join_request",
        "body": {"channel": "newchan"},
    }

    transport.handle_sys_message(msg, sender_nick="alice", connection=conn)

    conn.join.assert_called_once_with("#newchan")
    assert "newchan" in transport.joined_channels
