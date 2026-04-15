#!/usr/bin/env python3
"""zchat agent MCP server — 轻量 MCP stdio 代理.

每个 Claude Code agent 运行一个 agent_mcp 实例。
职责：MCP tools (reply/join/send_side_message) + IRC @mention 注入。
不持有 engine 组件（ConversationManager 等），所有状态在 channel-server 独立进程中。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any

import anyio
import mcp.server.stdio
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, TextContent, Tool

from message import chunk_message, clean_mention, detect_mention
from transport.irc_transport import IRCTransport

# ------------------------------------------------------------------ #
# 环境变量
# ------------------------------------------------------------------ #

AGENT_NAME = os.environ.get("AGENT_NAME", "agent0")
IRC_SERVER = os.environ.get("IRC_SERVER", "127.0.0.1")
IRC_PORT = int(os.environ.get("IRC_PORT", "6667"))
IRC_CHANNELS = os.environ.get("IRC_CHANNELS", "general")
IRC_TLS = os.environ.get("IRC_TLS", "false").lower() == "true"
IRC_AUTH_TOKEN = os.environ.get("IRC_AUTH_TOKEN", "")

# ------------------------------------------------------------------ #
# MCP notification 注入
# ------------------------------------------------------------------ #


async def inject_message(write_stream, msg: dict, context: str) -> None:
    """将 IRC 侧消息以 MCP notification 注入 Claude Code。"""
    ts = msg.get("ts", 0)
    iso_ts = (
        datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if ts
        else datetime.now(tz=timezone.utc).isoformat()
    )
    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/claude/channel",
        params={
            "content": msg.get("body", ""),
            "meta": {
                "chat_id": context,
                "message_id": msg.get("id", ""),
                "user": msg.get("nick", "unknown"),
                "ts": iso_ts,
            },
        },
    )
    await write_stream.send(SessionMessage(message=JSONRPCMessage(notification)))


async def poll_irc_queue(queue: asyncio.Queue, write_stream) -> None:
    """将 IRC 队列中的消息持续注入到 MCP 客户端。"""
    while True:
        msg, context = await queue.get()
        try:
            await inject_message(write_stream, msg, context)
        except Exception as e:
            print(f"[agent-mcp] inject error: {e}", file=sys.stderr)


# ------------------------------------------------------------------ #
# MCP Server 构造 + Tool 注册
# ------------------------------------------------------------------ #


def load_instructions(agent_name: str) -> str:
    path = Path(__file__).parent / "instructions.md"
    tmpl = Template(path.read_text(encoding="utf-8"))
    return tmpl.safe_substitute(agent_name=agent_name)


def create_server() -> Server:
    instructions = load_instructions(AGENT_NAME)
    return Server("zchat-agent-mcp", instructions=instructions)


def register_tools(server: Server, state: dict) -> None:
    """注册 MCP tools。IRC 连接从 state 字典懒解析。"""

    def _get_irc():
        conn = state.get("irc_connection")
        if conn is None:
            raise RuntimeError("IRC connection not initialized yet")
        return conn

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return [
            Tool(
                name="reply",
                description=(
                    "Reply to a user or channel. chat_id is a username for private "
                    "(e.g. 'alice') or #channel name (e.g. '#general')."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chat_id": {
                            "type": "string",
                            "description": "Target: username or #channel",
                        },
                        "text": {
                            "type": "string",
                            "description": "Message content",
                        },
                    },
                    "required": ["chat_id", "text"],
                },
            ),
            Tool(
                name="join_channel",
                description="Join an IRC channel to receive @mentions.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "channel_name": {
                            "type": "string",
                            "description": "Channel name without # prefix",
                        },
                    },
                    "required": ["channel_name"],
                },
            ),
            Tool(
                name="join_conversation",
                description=(
                    "Join an existing conversation channel (#conv-<id>)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "conversation_id": {"type": "string"},
                    },
                    "required": ["conversation_id"],
                },
            ),
            Tool(
                name="send_side_message",
                description=(
                    "Send a side-channel (operator+admin only) message to a conversation. "
                    "Uses __side: IRC prefix so channel-server routes with visibility=side."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "conversation_id": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["conversation_id", "text"],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "reply":
            return await _handle_reply(_get_irc(), arguments)
        if name == "join_channel":
            return await _handle_join_channel(_get_irc(), arguments)
        if name == "join_conversation":
            return await _handle_join_conversation(_get_irc(), arguments)
        if name == "send_side_message":
            return await _handle_send_side_message(_get_irc(), arguments)
        raise ValueError(f"Unknown tool: {name}")


# ------------------------------------------------------------------ #
# Tool handlers
# ------------------------------------------------------------------ #


async def _handle_reply(connection, arguments: dict) -> list[TextContent]:
    chat_id = arguments["chat_id"]
    text = arguments["text"]
    for chunk in chunk_message(text):
        connection.privmsg(chat_id, chunk)
    return [TextContent(type="text", text=f"Sent to {chat_id}")]


async def _handle_join_channel(connection, arguments: dict) -> list[TextContent]:
    channel = arguments["channel_name"]
    connection.join(f"#{channel}")
    return [TextContent(type="text", text=f"Joined #{channel}")]


async def _handle_join_conversation(
    connection, arguments: dict
) -> list[TextContent]:
    conv_id = arguments["conversation_id"]
    channel = IRCTransport.conv_channel_name(conv_id)
    try:
        connection.join(channel)
    except Exception as e:
        return [TextContent(type="text", text=f"join failed: {e}")]
    return [TextContent(type="text", text=f"joined {channel}")]


async def _handle_send_side_message(
    connection, arguments: dict
) -> list[TextContent]:
    conv_id = arguments["conversation_id"]
    text = arguments["text"]
    channel = IRCTransport.conv_channel_name(conv_id)
    # __side: 前缀让 channel-server 路由为 visibility=side
    prefixed = f"__side:{text}"
    for chunk in chunk_message(prefixed):
        connection.privmsg(channel, chunk)
    return [TextContent(type="text", text=f"side message sent to {conv_id}")]


# ------------------------------------------------------------------ #
# main
# ------------------------------------------------------------------ #


async def main() -> None:
    """启动 agent MCP server：MCP stdio + IRC @mention 注入。"""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    channels = [c.strip() for c in IRC_CHANNELS.split(",") if c.strip()]
    irc_transport = IRCTransport(
        server=IRC_SERVER,
        port=IRC_PORT,
        nick=AGENT_NAME,
        channels=channels,
        tls=IRC_TLS,
        auth_token=IRC_AUTH_TOKEN,
    )

    server = create_server()
    state: dict = {}
    register_tools(server, state)

    def _on_pubmsg(conn, event):
        nick = event.source.nick
        body = event.arguments[0]
        if not detect_mention(body, AGENT_NAME):
            return
        cleaned = clean_mention(body, AGENT_NAME)
        channel = event.target
        msg = {
            "id": os.urandom(4).hex(),
            "nick": nick,
            "type": "msg",
            "body": cleaned,
            "ts": time.time(),
        }
        print(f"[agent-mcp] [{channel}] {nick}: {body}", file=sys.stderr)
        loop.call_soon_threadsafe(queue.put_nowait, (msg, channel))

    def _on_privmsg(nick: str, body: str):
        msg = {
            "id": os.urandom(4).hex(),
            "nick": nick,
            "type": "msg",
            "body": body,
            "ts": time.time(),
        }
        print(f"[agent-mcp] [private:{nick}] {nick}: {body}", file=sys.stderr)
        loop.call_soon_threadsafe(queue.put_nowait, (msg, nick))

    init_opts = InitializationOptions(
        server_name=f"zchat-agent-{AGENT_NAME}",
        server_version="1.0.0",
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={"claude/channel": {}},
        ),
    )

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await anyio.sleep(2)
        connection = irc_transport.start(
            queue,
            loop,
            on_pubmsg=_on_pubmsg,
            on_privmsg_text=_on_privmsg,
        )
        state["irc_connection"] = connection
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(server.run, read_stream, write_stream, init_opts)
                tg.start_soon(poll_irc_queue, queue, write_stream)
        finally:
            irc_transport.disconnect("Agent shutting down")


def entry_point() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    entry_point()
