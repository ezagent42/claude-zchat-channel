"""Unit 测试 server.py 的集成点（mock IRC, mock MCP stdio）。"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CS_DB_PATH", str(tmp_path / "conv.db"))
    monkeypatch.setenv("CS_EVENT_DB_PATH", str(tmp_path / "events.db"))
    monkeypatch.setenv("CS_MESSAGE_DB_PATH", str(tmp_path / "msg.db"))
    monkeypatch.setenv("BRIDGE_PORT", "0")
    monkeypatch.setenv("AGENT_NAME", "unit-agent")


def test_register_tools_lists_four_tools() -> None:
    """handle_list_tools 应返回 4 个 agent MCP tool。"""
    import agent_mcp

    srv = agent_mcp.create_server()
    state: dict = {}
    agent_mcp.register_tools(srv, state)

    handler = srv.request_handlers
    from mcp.types import ListToolsRequest

    req = ListToolsRequest(method="tools/list", params=None)
    result = asyncio.run(handler[ListToolsRequest](req))
    tools = result.root.tools
    names = {t.name for t in tools}
    assert names == {
        "reply",
        "join_channel",
        "join_conversation",
        "send_side_message",
    }


def test_main_builds_components() -> None:
    """build_components() 应能组装所有 engine/bridge/transport 模块，不抛异常。"""
    import server

    components = server.build_components()
    assert components["event_bus"] is not None
    assert components["conversation_manager"] is not None
    assert components["mode_manager"] is not None
    assert components["timer_manager"] is not None
    assert components["participant_registry"] is not None
    assert components["message_store"] is not None
    assert components["bridge_server"] is not None
    assert components["irc_transport"] is not None

    # 清理 DB
    components["event_bus"].close()
    components["conversation_manager"].close_db()
    components["message_store"].close()


def test_bridge_customer_connect_creates_conversation(tmp_path) -> None:
    """
    BridgeAPIServer._handle_customer_connect 必须能用已修正签名
    调用 ConversationManager.create(conversation_id, metadata=...)
    （customer 被放进 metadata）。
    """
    from bridge_api.ws_server import BridgeAPIServer
    from engine.conversation_manager import ConversationManager

    cm = ConversationManager(str(tmp_path / "conv.db"))
    bs = BridgeAPIServer(conversation_manager=cm, port=0)

    bs._handle_customer_connect(
        {
            "conversation_id": "conv1",
            "customer": {"id": "david", "name": "David"},
            "metadata": {"source": "feishu"},
        }
    )

    conv = cm.get("conv1")
    assert conv is not None
    assert conv.id == "conv1"
    assert conv.metadata.get("customer", {}).get("id") == "david"
    assert conv.metadata.get("source") == "feishu"
    cm.close_db()


def test_reply_tool_sends_message() -> None:
    """reply tool 调用应通过 IRC 发送消息。"""
    import agent_mcp
    from mcp.types import CallToolRequest, CallToolRequestParams

    srv = agent_mcp.create_server()
    state: dict = {}
    mock_conn = MagicMock()
    state["irc_connection"] = mock_conn
    agent_mcp.register_tools(srv, state)

    handler = srv.request_handlers
    req = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="reply",
            arguments={"chat_id": "#general", "text": "hello"},
        ),
    )
    result = asyncio.run(handler[CallToolRequest](req))
    content = result.root.content
    assert len(content) == 1
    assert content[0].type == "text"
    assert "sent_to" in content[0].text
    mock_conn.privmsg.assert_called()


# TC-U15: wire_bridge_callbacks 注入了 on_operator_join + on_operator_command
def test_build_components_injects_operator_callbacks() -> None:
    """
    server.wire_bridge_callbacks(bridge_server, components) 注入后
    on_operator_join 和 on_operator_command 均非 None。
    """
    import server

    components = server.build_components()
    bridge_server = components["bridge_server"]

    server.wire_bridge_callbacks(bridge_server, components)

    assert bridge_server.on_operator_join is not None, (
        "on_operator_join should be wired by wire_bridge_callbacks()"
    )
    assert bridge_server.on_operator_command is not None, (
        "on_operator_command should be wired by wire_bridge_callbacks()"
    )

    components["event_bus"].close()
    components["conversation_manager"].close_db()
    components["message_store"].close()
