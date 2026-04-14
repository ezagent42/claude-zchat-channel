#!/usr/bin/env python3
"""zchat-channel-server v1.0 — 集成入口.

IRC 侧通过 `transport.irc_transport.IRCTransport` 连接，
Bridge 侧通过 `bridge_api.ws_server.BridgeAPIServer` 暴露 WebSocket，
核心业务由 `engine/` 各组件承载，协议原语在 `protocol/`。
server.py 只是胶水代码：组装所有组件并启动 MCP stdio + Bridge + IRC。
"""
from __future__ import annotations

import asyncio
import json
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

from bridge_api.ws_server import BridgeAPIServer
from engine.conversation_manager import ConversationManager
from engine.event_bus import EventBus
from engine.message_store import MessageStore
from engine.mode_manager import ModeManager
from engine.participant_registry import ParticipantRegistry
from engine.timer_manager import TimerManager
from message import chunk_message, clean_mention, detect_mention
from protocol.mode import ConversationMode
from protocol.participant import Participant, ParticipantRole
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

BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "9999"))
BRIDGE_HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")

CS_DB_PATH = os.environ.get("CS_DB_PATH", "conversations.db")
CS_EVENT_DB_PATH = os.environ.get(
    "CS_EVENT_DB_PATH", CS_DB_PATH.replace(".db", "_events.db")
)
CS_MESSAGE_DB_PATH = os.environ.get(
    "CS_MESSAGE_DB_PATH", CS_DB_PATH.replace(".db", "_messages.db")
)

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
            print(f"[channel-server] inject error: {e}", file=sys.stderr)


# ------------------------------------------------------------------ #
# MCP Server 构造 + Tool 注册
# ------------------------------------------------------------------ #


def load_instructions(agent_name: str) -> str:
    path = Path(__file__).parent / "instructions.md"
    tmpl = Template(path.read_text(encoding="utf-8"))
    return tmpl.safe_substitute(agent_name=agent_name)


def create_server() -> Server:
    instructions = load_instructions(AGENT_NAME)
    return Server("zchat-channel", instructions=instructions)


def register_tools(server: Server, state: dict) -> None:
    """注册 MCP tools。IRC 连接 / 组件从 state 字典懒解析。"""

    def _get_irc():
        conn = state.get("irc_connection")
        if conn is None:
            raise RuntimeError("IRC connection not initialized yet")
        return conn

    def _components() -> dict:
        return state.get("components") or {}

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
                name="edit_message",
                description=(
                    "Edit a previously sent message by its message_id."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "new_content": {"type": "string"},
                    },
                    "required": ["message_id", "new_content"],
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
                    "Send a side-channel (operator+admin only) message to a conversation."
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
            Tool(
                name="list_conversations",
                description="List currently active conversations.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            ),
            Tool(
                name="get_conversation_status",
                description="Get status details of a conversation by id.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "conversation_id": {"type": "string"},
                    },
                    "required": ["conversation_id"],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "reply":
            return await _handle_reply(_get_irc(), arguments)
        if name == "join_channel":
            return await _handle_join_channel(_get_irc(), arguments)
        if name == "edit_message":
            return await _handle_edit_message(_components(), arguments)
        if name == "join_conversation":
            return await _handle_join_conversation(
                _get_irc(), _components(), arguments
            )
        if name == "send_side_message":
            return await _handle_send_side_message(_components(), arguments)
        if name == "list_conversations":
            return await _handle_list_conversations(_components(), arguments)
        if name == "get_conversation_status":
            return await _handle_get_conversation_status(_components(), arguments)
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


async def _handle_edit_message(
    components: dict, arguments: dict
) -> list[TextContent]:
    store: MessageStore | None = components.get("message_store")
    if store is None:
        return [TextContent(type="text", text="message_store unavailable")]
    message_id = arguments["message_id"]
    new_content = arguments["new_content"]
    original = store.get(message_id)
    if original is None:
        return [TextContent(type="text", text=f"message {message_id} not found")]
    edited = store.edit(message_id, new_content)
    return [TextContent(type="text", text=f"edited {message_id} → {edited.id}")]


async def _handle_join_conversation(
    connection, components: dict, arguments: dict
) -> list[TextContent]:
    conv_id = arguments["conversation_id"]
    channel = IRCTransport.conv_channel_name(conv_id)
    try:
        connection.join(channel)
    except Exception as e:
        return [TextContent(type="text", text=f"join failed: {e}")]
    return [TextContent(type="text", text=f"joined {channel}")]


async def _handle_send_side_message(
    components: dict, arguments: dict
) -> list[TextContent]:
    bs: BridgeAPIServer | None = components.get("bridge_server")
    conv_id = arguments["conversation_id"]
    text = arguments["text"]
    if bs is None:
        return [TextContent(type="text", text="bridge_server unavailable")]
    await bs.send_reply(
        conversation_id=conv_id,
        text=text,
        visibility="side",
    )
    return [TextContent(type="text", text=f"side message sent to {conv_id}")]


async def _handle_list_conversations(
    components: dict, arguments: dict
) -> list[TextContent]:
    cm: ConversationManager | None = components.get("conversation_manager")
    if cm is None:
        return [TextContent(type="text", text="[]")]
    convs = cm.list_active()
    payload = [
        {"id": c.id, "state": c.state.value, "mode": c.mode}
        for c in convs
    ]
    return [TextContent(type="text", text=json.dumps(payload))]


async def _handle_get_conversation_status(
    components: dict, arguments: dict
) -> list[TextContent]:
    cm: ConversationManager | None = components.get("conversation_manager")
    if cm is None:
        return [TextContent(type="text", text="conversation_manager unavailable")]
    conv_id = arguments["conversation_id"]
    conv = cm.get(conv_id)
    if conv is None:
        return [TextContent(type="text", text=f"conversation {conv_id} not found")]
    payload = {
        "id": conv.id,
        "state": conv.state.value,
        "mode": conv.mode,
        "participants": [
            getattr(p, "id", None) for p in conv.participants
        ],
    }
    return [TextContent(type="text", text=json.dumps(payload))]


# ------------------------------------------------------------------ #
# Bridge 回调注入
# ------------------------------------------------------------------ #


def wire_bridge_callbacks(
    bridge_server: BridgeAPIServer, components: dict[str, Any]
) -> None:
    """将业务回调注入到 BridgeAPIServer 的钩子槽中。

    从 main() 中独立出来便于单元测试。
    """
    conv_manager: ConversationManager = components["conversation_manager"]
    mode_manager: ModeManager = components["mode_manager"]

    async def _on_operator_join(msg: dict) -> None:
        """Operator 通过 Bridge 加入对话 → 注册参与者 + 触发模式切换。"""
        conv_id = msg.get("conversation_id", "")
        operator = msg.get("operator", {})
        operator_id = operator.get("id", "unknown")

        conv = conv_manager.get(conv_id)
        if conv is None:
            print(
                f"[server] operator_join: conversation {conv_id!r} not found",
                file=sys.stderr,
            )
            return

        # 注册 operator 参与者
        participant = Participant(id=operator_id, role=ParticipantRole.OPERATOR)
        try:
            conv_manager.add_participant(conv_id, participant)
        except Exception as e:
            print(f"[server] add_participant failed: {e}", file=sys.stderr)

        # 模式切换：auto → copilot（仅当前为 auto 时）
        if conv.mode == ConversationMode.AUTO.value:
            try:
                t = await mode_manager.atransition(
                    conv,
                    ConversationMode.COPILOT,
                    trigger="operator_join",
                    triggered_by=operator_id,
                )
                await bridge_server.send_event(
                    "mode.changed",
                    {"from": t.from_mode.value, "to": t.to_mode.value,
                     "trigger": "operator_join", "triggered_by": operator_id},
                    conv_id,
                )
            except Exception as e:
                print(f"[server] mode transition failed: {e}", file=sys.stderr)

    async def _on_operator_command(msg: dict, cmd: Any) -> None:
        """Operator 发送命令（/hijack / /release / /copilot）→ 模式切换。"""
        conv_id = msg.get("conversation_id", "")
        operator_id = msg.get("operator_id", "unknown")

        conv = conv_manager.get(conv_id)
        if conv is None:
            return

        target_mode: ConversationMode | None = None
        if cmd.name == "hijack":
            target_mode = ConversationMode.TAKEOVER
        elif cmd.name == "release":
            target_mode = ConversationMode.AUTO
        elif cmd.name == "copilot":
            target_mode = ConversationMode.COPILOT

        if target_mode is None:
            return

        try:
            t = await mode_manager.atransition(
                conv,
                target_mode,
                trigger=cmd.name,
                triggered_by=operator_id,
            )
            await bridge_server.send_event(
                "mode.changed",
                {"from": t.from_mode.value, "to": t.to_mode.value,
                 "trigger": cmd.name, "triggered_by": operator_id},
                conv_id,
            )
            # hijack 后发出 side visibility 系统通知（E2E gate enforcement 验证路径）
            if cmd.name == "hijack":
                await bridge_server.send_reply(
                    conversation_id=conv_id,
                    text=f"[system] takeover activated by {operator_id}",
                    visibility="side",
                )
        except Exception as e:
            print(f"[server] command {cmd.name} failed: {e}", file=sys.stderr)

    bridge_server.on_operator_join = _on_operator_join
    bridge_server.on_operator_command = _on_operator_command


# ------------------------------------------------------------------ #
# 组件组装
# ------------------------------------------------------------------ #


def build_components() -> dict[str, Any]:
    """组装所有 engine / bridge / transport 组件。不启动 IRC / WebSocket。"""
    event_bus = EventBus(CS_EVENT_DB_PATH)
    conversation_manager = ConversationManager(CS_DB_PATH)
    mode_manager = ModeManager(event_bus)
    timer_manager = TimerManager(event_bus)
    participant_registry = ParticipantRegistry()
    message_store = MessageStore(CS_MESSAGE_DB_PATH)
    bridge_server = BridgeAPIServer(
        conversation_manager=conversation_manager,
        port=BRIDGE_PORT,
        host=BRIDGE_HOST,
    )
    channels = [c.strip() for c in IRC_CHANNELS.split(",") if c.strip()]
    irc_transport = IRCTransport(
        server=IRC_SERVER,
        port=IRC_PORT,
        nick=AGENT_NAME,
        channels=channels,
        tls=IRC_TLS,
        auth_token=IRC_AUTH_TOKEN,
    )
    return {
        "event_bus": event_bus,
        "conversation_manager": conversation_manager,
        "mode_manager": mode_manager,
        "timer_manager": timer_manager,
        "participant_registry": participant_registry,
        "message_store": message_store,
        "bridge_server": bridge_server,
        "irc_transport": irc_transport,
    }


# ------------------------------------------------------------------ #
# main
# ------------------------------------------------------------------ #


async def main() -> None:
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    components = build_components()
    irc_transport: IRCTransport = components["irc_transport"]
    bridge_server: BridgeAPIServer = components["bridge_server"]

    server = create_server()
    state: dict = {"components": components}
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
        print(f"[channel-server] [{channel}] {nick}: {body}", file=sys.stderr)
        loop.call_soon_threadsafe(queue.put_nowait, (msg, channel))

    def _on_privmsg(nick: str, body: str):
        msg = {
            "id": os.urandom(4).hex(),
            "nick": nick,
            "type": "msg",
            "body": body,
            "ts": time.time(),
        }
        print(f"[channel-server] [private:{nick}] {nick}: {body}", file=sys.stderr)
        loop.call_soon_threadsafe(queue.put_nowait, (msg, nick))

    init_opts = InitializationOptions(
        server_name=f"zchat-channel-{AGENT_NAME}",
        server_version="1.0.0",
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={"claude/channel": {}},
        ),
    )

    # 注入业务回调（mode switching / command dispatch）
    wire_bridge_callbacks(bridge_server, components)

    # 先启 Bridge WebSocket（无阻塞），E2E 测试依赖这个先就绪
    await bridge_server.start()

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
            await bridge_server.stop()


def entry_point() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    entry_point()
