#!/usr/bin/env python3
"""
zchat-channel-server: Claude Code Channel MCP Server
Bridges IRC messaging <-> Claude Code via MCP stdio protocol.
"""
import asyncio
import json
import os
import sys
import time
import threading
from zchat_protocol.sys_messages import (
    is_sys_message, make_sys_message,
    encode_sys_for_irc, decode_sys_from_irc,
)

import anyio
import irc.client
import mcp.server.stdio
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, Tool, TextContent

from message import detect_mention, clean_mention, chunk_message
from datetime import datetime, timezone

AGENT_NAME = os.environ.get("AGENT_NAME", "agent0")
IRC_SERVER = os.environ.get("IRC_SERVER", "127.0.0.1")
IRC_PORT = int(os.environ.get("IRC_PORT", "6667"))
IRC_CHANNELS = os.environ.get("IRC_CHANNELS", "general")
IRC_TLS = os.environ.get("IRC_TLS", "false").lower() == "true"
_msg_counter = {"sent": 0, "received": 0}

# ============================================================
# MCP Notification Injection
# ============================================================

async def inject_message(write_stream, msg: dict, context: str):
    """Send a channel notification to Claude Code via the MCP write stream."""
    ts = msg.get("ts", 0)
    iso_ts = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else datetime.now(tz=timezone.utc).isoformat()
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


async def poll_irc_queue(queue: asyncio.Queue, write_stream):
    """Consume IRC messages from the queue and inject into Claude Code."""
    while True:
        msg, context = await queue.get()
        try:
            await inject_message(write_stream, msg, context)
        except Exception as e:
            print(f"[channel-server] inject error: {e}", file=sys.stderr)

# ============================================================
# IRC Setup
# ============================================================

def setup_irc(queue: asyncio.Queue, loop: asyncio.AbstractEventLoop):
    """Initialize IRC client, connect, subscribe to channels."""
    reactor = irc.client.Reactor()
    connection = reactor.server().connect(
        IRC_SERVER, IRC_PORT, AGENT_NAME,
    )
    joined_channels: set[str] = set()

    def on_welcome(conn, event):
        """Auto-join channels on connect."""
        channels = IRC_CHANNELS.split(",")
        for ch in channels:
            ch = ch.strip().lstrip("#")
            if ch:
                conn.join(f"#{ch}")
                joined_channels.add(ch)
                print(f"[channel-server] Joined #{ch}", file=sys.stderr)
        print(f"[channel-server] {AGENT_NAME} ready on IRC ({IRC_SERVER}:{IRC_PORT})", file=sys.stderr)

    def on_pubmsg(conn, event):
        """Handle channel messages — filter for @mentions."""
        nick = event.source.nick
        if nick == AGENT_NAME:
            return
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
        print(f"[channel-server] [{channel}] {nick}: {body}", file=sys.stderr)
        loop.call_soon_threadsafe(queue.put_nowait, (msg, channel))
        _msg_counter["received"] += 1

    def on_privmsg(conn, event):
        """Handle private messages and sys messages."""
        nick = event.source.nick
        if nick == AGENT_NAME:
            return
        body = event.arguments[0]
        # Check for sys message (__zchat_sys: prefix)
        sys_msg = decode_sys_from_irc(body)
        if sys_msg is not None:
            _handle_sys_message(sys_msg, nick, conn, joined_channels)
            return
        # Regular private message
        msg = {
            "id": os.urandom(4).hex(),
            "nick": nick,
            "type": "msg",
            "body": body,
            "ts": time.time(),
        }
        print(f"[channel-server] [private:{nick}] {nick}: {body}", file=sys.stderr)
        loop.call_soon_threadsafe(queue.put_nowait, (msg, nick))
        _msg_counter["received"] += 1

    def on_disconnect(conn, event):
        """Handle disconnection — attempt reconnect."""
        print(f"[channel-server] Disconnected from IRC, reconnecting in 5s...", file=sys.stderr)
        time.sleep(5)
        try:
            conn.reconnect()
        except Exception as e:
            print(f"[channel-server] Reconnect failed: {e}", file=sys.stderr)

    connection.add_global_handler("welcome", on_welcome)
    connection.add_global_handler("pubmsg", on_pubmsg)
    connection.add_global_handler("privmsg", on_privmsg)
    connection.add_global_handler("disconnect", on_disconnect)

    # Run IRC reactor in a separate thread
    def irc_thread():
        try:
            reactor.process_forever()
        except Exception as e:
            print(f"[channel-server] IRC reactor error: {e}", file=sys.stderr)

    thread = threading.Thread(target=irc_thread, daemon=True)
    thread.start()

    return connection, joined_channels

# ============================================================
# Sys Message Handling
# ============================================================

def _handle_sys_message(msg: dict, sender_nick: str, connection, joined_channels: set):
    """Handle incoming system messages over IRC PRIVMSG."""
    msg_type = msg.get("type", "")
    if msg_type == "sys.stop_request":
        reply = make_sys_message(AGENT_NAME, "sys.stop_confirmed", {}, ref_id=msg["id"])
        connection.privmsg(sender_nick, encode_sys_for_irc(reply))
    elif msg_type == "sys.join_request":
        channel = msg.get("body", {}).get("channel", "").lstrip("#")
        if channel:
            connection.join(f"#{channel}")
            joined_channels.add(channel)
            reply = make_sys_message(AGENT_NAME, "sys.join_confirmed",
                                     {"channel": f"#{channel}"}, ref_id=msg["id"])
            connection.privmsg(sender_nick, encode_sys_for_irc(reply))
    elif msg_type == "sys.status_request":
        reply = make_sys_message(AGENT_NAME, "sys.status_response", {
            "channels": list(joined_channels),
            "messages_sent": _msg_counter["sent"],
            "messages_received": _msg_counter["received"],
        }, ref_id=msg["id"])
        connection.privmsg(sender_nick, encode_sys_for_irc(reply))

# ============================================================
# MCP Server + Tools
# ============================================================

CHANNEL_INSTRUCTIONS = f"""You are {AGENT_NAME}, a Claude Code agent connected to an IRC chat system.

Messages arrive as <channel source="zchat-channel" chat_id="..." user="..." ts="...">content</channel>.
- chat_id starting with "#" is a channel message (e.g. "#general")
- chat_id without "#" is a private message from that user

When you receive a channel notification:
1. Read the message content and the user who sent it
2. If addressed to you or relevant, respond using the "reply" tool with the same chat_id
3. For private messages requesting you to stop/exit, save any work and run /exit

## Available slash commands

Users in your session can use these shortcuts instead of describing actions in natural language:

| Command | Description |
|---------|-------------|
| `/zchat:reply -c #general -t "hello"` | Reply to a channel or user |
| `/zchat:join -c dev` | Join an IRC channel |
| `/zchat:dm -u alice -t "hey"` | Send a private message |
| `/zchat:broadcast -t "deploying"` | Send to all joined channels |

When these commands are invoked, follow the command instructions to call the appropriate MCP tool.
You can also call "reply" and "join_channel" tools directly when responding to channel messages."""


def create_server():
    server = Server("zchat-channel", instructions=CHANNEL_INSTRUCTIONS)
    return server

def register_tools(server: Server, state: dict):
    """Register MCP tools. IRC connection is resolved lazily from state dict."""

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
                description="Reply to a user or channel. chat_id is a username for private (e.g. 'alice') or #channel name (e.g. '#general').",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chat_id": {"type": "string", "description": "Target: username or #channel"},
                        "text": {"type": "string", "description": "Message content"},
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
                        "channel_name": {"type": "string", "description": "Channel name without # prefix"},
                    },
                    "required": ["channel_name"],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        conn = _get_irc()
        if name == "reply":
            return await _handle_reply(conn, arguments)
        elif name == "join_channel":
            return await _handle_join_channel(conn, arguments)
        raise ValueError(f"Unknown tool: {name}")

async def _handle_reply(connection, arguments: dict) -> list[TextContent]:
    chat_id = arguments["chat_id"]
    text = arguments["text"]
    chunks = chunk_message(text)
    for chunk in chunks:
        target = chat_id  # #channel or nick
        connection.privmsg(target, chunk)
    _msg_counter["sent"] += 1
    return [TextContent(type="text", text=f"Sent to {chat_id}")]

async def _handle_join_channel(connection, arguments: dict) -> list[TextContent]:
    channel = arguments["channel_name"]
    connection.join(f"#{channel}")
    return [TextContent(type="text", text=f"Joined #{channel}")]


# ============================================================
# Main
# ============================================================

async def main():
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    server = create_server()
    state: dict = {}
    register_tools(server, state)
    init_opts = InitializationOptions(
        server_name=f"zchat-channel-{AGENT_NAME}",
        server_version="0.2.0",
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={"claude/channel": {}},
        ),
    )
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await anyio.sleep(2)
        connection, joined_channels = setup_irc(queue, loop)
        state["irc_connection"] = connection
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(server.run, read_stream, write_stream, init_opts)
                tg.start_soon(poll_irc_queue, queue, write_stream)
        finally:
            connection.disconnect("Agent shutting down")

def entry_point():
    """Synchronous entry point for console_scripts."""
    asyncio.run(main())

if __name__ == "__main__":
    entry_point()
