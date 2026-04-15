"""Unit 测试 server.py / agent_mcp.py 架构拆分。

验证拆分后两个模块职责清晰、entry_points 正确。
"""
from __future__ import annotations

import asyncio
import importlib
import os
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("CS_DB_PATH", str(tmp_path / "conv.db"))
    monkeypatch.setenv("CS_EVENT_DB_PATH", str(tmp_path / "events.db"))
    monkeypatch.setenv("CS_MESSAGE_DB_PATH", str(tmp_path / "msg.db"))
    monkeypatch.setenv("BRIDGE_PORT", "0")
    monkeypatch.setenv("AGENT_NAME", "unit-agent")


# ------------------------------------------------------------------ #
# server.py — 独立进程模块
# ------------------------------------------------------------------ #


def test_server_has_no_mcp_imports() -> None:
    """server.py 拆分后不应包含 MCP 相关代码。"""
    import server

    importlib.reload(server)
    # server 模块不应有 mcp 相关属性
    assert not hasattr(server, "create_server"), "create_server should be in agent_mcp"
    assert not hasattr(server, "register_tools"), "register_tools should be in agent_mcp"
    assert not hasattr(server, "inject_message"), "inject_message should be in agent_mcp"
    assert not hasattr(server, "poll_irc_queue"), "poll_irc_queue should be in agent_mcp"
    assert not hasattr(server, "load_instructions"), "load_instructions should be in agent_mcp"


def test_server_has_core_functions() -> None:
    """server.py 保留 build_components + wire_bridge_callbacks。"""
    import server

    importlib.reload(server)
    assert hasattr(server, "build_components")
    assert hasattr(server, "wire_bridge_callbacks")
    assert hasattr(server, "entry_point")
    assert hasattr(server, "main")


def test_server_build_components_works() -> None:
    """build_components() 仍能组装所有 engine 组件。"""
    import server

    importlib.reload(server)
    components = server.build_components()
    assert components["event_bus"] is not None
    assert components["conversation_manager"] is not None
    assert components["mode_manager"] is not None
    assert components["bridge_server"] is not None
    assert components["irc_transport"] is not None

    components["event_bus"].close()
    components["conversation_manager"].close_db()
    components["message_store"].close()


# ------------------------------------------------------------------ #
# agent_mcp.py — 轻量 MCP 模块
# ------------------------------------------------------------------ #


def test_agent_mcp_has_mcp_functions() -> None:
    """agent_mcp.py 应包含所有 MCP 相关代码。"""
    import agent_mcp

    assert hasattr(agent_mcp, "create_server")
    assert hasattr(agent_mcp, "register_tools")
    assert hasattr(agent_mcp, "inject_message")
    assert hasattr(agent_mcp, "poll_irc_queue")
    assert hasattr(agent_mcp, "load_instructions")
    assert hasattr(agent_mcp, "entry_point")


def test_agent_mcp_has_no_engine_imports() -> None:
    """agent_mcp.py 不应持有 engine 组件（ConversationManager 等）。"""
    import agent_mcp

    assert not hasattr(agent_mcp, "build_components"), \
        "build_components belongs in server.py"
    assert not hasattr(agent_mcp, "wire_bridge_callbacks"), \
        "wire_bridge_callbacks belongs in server.py"


def test_agent_mcp_tools_are_four() -> None:
    """agent_mcp 应注册 4 个 tool: reply, join_channel, join_conversation, send_side_message。"""
    import agent_mcp

    srv = agent_mcp.create_server()
    state: dict = {}
    agent_mcp.register_tools(srv, state)

    from mcp.types import ListToolsRequest

    handler = srv.request_handlers
    req = ListToolsRequest(method="tools/list", params=None)
    result = asyncio.run(handler[ListToolsRequest](req))
    tools = result.root.tools
    names = {t.name for t in tools}
    assert names == {"reply", "join_channel", "join_conversation", "send_side_message"}


# ------------------------------------------------------------------ #
# Entry Points
# ------------------------------------------------------------------ #


def test_entry_points_resolve() -> None:
    """zchat-channel 和 zchat-agent-mcp 两个 entry_point 均可解析。"""
    from importlib.metadata import entry_points

    eps = entry_points(group="console_scripts")
    ep_names = {e.name for e in eps}
    assert "zchat-channel" in ep_names
    assert "zchat-agent-mcp" in ep_names
