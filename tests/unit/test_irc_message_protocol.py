"""Unit 测试 IRC 消息协议：agent_mcp 前缀生成 + channel-server 前缀解析。"""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock

import pytest

from transport.irc_transport import parse_agent_message


# ------------------------------------------------------------------ #
# parse_agent_message — channel-server 解析
# ------------------------------------------------------------------ #


def test_cs_parse_edit_prefix() -> None:
    """TC-004: __edit:msg_001:替换内容 → type=edit, message_id=msg_001。"""
    result = parse_agent_message("__edit:msg_001:替换后的完整内容")
    assert result["type"] == "edit"
    assert result["message_id"] == "msg_001"
    assert result["text"] == "替换后的完整内容"


def test_cs_parse_side_prefix() -> None:
    """TC-005: __side:建议内容 → type=side, text=建议内容。"""
    result = parse_agent_message("__side:这是一条建议")
    assert result["type"] == "side"
    assert result["text"] == "这是一条建议"


def test_cs_parse_msg_prefix() -> None:
    """__msg:uuid:内容 → type=reply, message_id=uuid。"""
    msg_id = str(uuid.uuid4())
    result = parse_agent_message(f"__msg:{msg_id}:你好")
    assert result["type"] == "reply"
    assert result["message_id"] == msg_id
    assert result["text"] == "你好"


def test_cs_parse_no_prefix() -> None:
    """TC-006: 普通消息 → type=reply, 无 message_id。"""
    result = parse_agent_message("这是普通消息")
    assert result["type"] == "reply"
    assert result["text"] == "这是普通消息"
    assert "message_id" not in result


def test_cs_parse_edit_no_colon_fallback() -> None:
    """__edit: 后没有冒号分隔 → fallback 为普通消息。"""
    result = parse_agent_message("__edit:malformed")
    assert result["type"] == "reply"


def test_cs_parse_edit_with_colons_in_text() -> None:
    """文本中含冒号不影响解析（只按第一个冒号分割）。"""
    result = parse_agent_message("__edit:msg_002:价格是 100:200:300")
    assert result["type"] == "edit"
    assert result["message_id"] == "msg_002"
    assert result["text"] == "价格是 100:200:300"


# ------------------------------------------------------------------ #
# agent_mcp reply — 前缀生成
# ------------------------------------------------------------------ #


def test_reply_returns_message_id() -> None:
    """TC-001: reply() 返回非空 UUID message_id。"""
    import agent_mcp

    mock_conn = MagicMock()
    result = asyncio.run(
        agent_mcp._handle_reply(
            mock_conn, {"chat_id": "#conv-test", "text": "hello"}
        )
    )
    response = json.loads(result[0].text)
    assert "message_id" in response
    # 验证是有效 UUID
    uuid.UUID(response["message_id"])


def test_reply_edit_irc_prefix() -> None:
    """TC-002: reply(edit_of='msg_001') 生成 __edit:msg_001:text。"""
    import agent_mcp

    mock_conn = MagicMock()
    asyncio.run(
        agent_mcp._handle_reply(
            mock_conn,
            {"chat_id": "#conv-test", "text": "更新内容", "edit_of": "msg_001"},
        )
    )
    mock_conn.privmsg.assert_called_once()
    sent_text = mock_conn.privmsg.call_args[0][1]
    assert sent_text.startswith("__edit:msg_001:")
    assert "更新内容" in sent_text


def test_reply_side_irc_prefix() -> None:
    """TC-003: reply(side=True) 生成 __side:text。"""
    import agent_mcp

    mock_conn = MagicMock()
    asyncio.run(
        agent_mcp._handle_reply(
            mock_conn,
            {"chat_id": "#conv-test", "text": "建议", "side": True},
        )
    )
    mock_conn.privmsg.assert_called_once()
    sent_text = mock_conn.privmsg.call_args[0][1]
    assert sent_text == "__side:建议"


def test_reply_normal_uses_msg_prefix() -> None:
    """普通 reply 使用 __msg:<uuid>:<text> 前缀。"""
    import agent_mcp

    mock_conn = MagicMock()
    asyncio.run(
        agent_mcp._handle_reply(
            mock_conn, {"chat_id": "#conv-test", "text": "你好"}
        )
    )
    mock_conn.privmsg.assert_called_once()
    sent_text = mock_conn.privmsg.call_args[0][1]
    assert sent_text.startswith("__msg:")
    assert sent_text.endswith(":你好")
    # 提取 UUID 部分验证
    parts = sent_text.split(":", 2)  # __msg, uuid, text
    uuid.UUID(parts[1])
