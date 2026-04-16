#!/usr/bin/env python3
"""zchat-channel-server — 纯 glue 入口：组装 engine 组件 + 注入回调 + 启动运行时。

业务逻辑在 engine/ (CommandHandler, MessageRouter, ModeManager 等)。
MCP Server 在 agent_mcp.py。
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any

from bridge_api.ws_server import BridgeAPIServer
from engine.command_handler import CommandHandler
from engine.conversation_manager import ConversationManager
from engine.event_bus import EventBus
from engine.message_router import MessageRouter
from engine.message_store import MessageStore
from engine.mode_manager import ModeManager
from engine.participant_registry import ParticipantRegistry
from engine.squad_registry import SquadRegistry
from engine.timer_manager import TimerManager
from plugins.manager import PluginManager
from zchat_protocol.event import Event, EventType
from routing_config import RoutingConfig, load_routing_config
from transport.irc_transport import IRCTransport

_e = os.environ.get
AGENT_NAME = _e("AGENT_NAME", "cs-bot")
IRC_SERVER, IRC_PORT = _e("IRC_SERVER", "127.0.0.1"), int(_e("IRC_PORT", "6667"))
IRC_CHANNELS, IRC_TLS = _e("IRC_CHANNELS", "general"), _e("IRC_TLS", "false").lower() == "true"
IRC_AUTH_TOKEN = _e("IRC_AUTH_TOKEN", "")
BRIDGE_PORT, BRIDGE_HOST = int(_e("BRIDGE_PORT", "9999")), _e("BRIDGE_HOST", "127.0.0.1")
CS_DB_PATH = _e("CS_DB_PATH", "conversations.db")
CS_ROUTING_CONFIG = _e("CS_ROUTING_CONFIG", "routing.toml")
CS_PLUGINS_DIR = _e("CS_PLUGINS_DIR", str(__import__("pathlib").Path(__file__).parent / "plugins"))


def wire_bridge_callbacks(
    bridge_server: BridgeAPIServer,
    components: dict[str, Any],
    routing_config: RoutingConfig | None = None,
) -> None:
    """将业务回调注入到 BridgeAPIServer 的钩子槽 — 纯薄代理，逻辑在 CommandHandler。"""
    conv_manager: ConversationManager = components["conversation_manager"]
    rc = routing_config or RoutingConfig()
    cmd = CommandHandler(
        conv_manager=conv_manager, mode_manager=components["mode_manager"],
        event_bus=components["event_bus"], message_store=components["message_store"],
        bridge_server=bridge_server, squad_registry=components["squad_registry"],
        routing_config=rc,
    )
    msg_router = MessageRouter(
        conv_manager, components.get("message_store"), bridge_server,
        irc_transport=components.get("irc_transport"),
    )

    # thin delegations — 保持回调签名不变
    async def _on_operator_join(msg: dict) -> None:
        await cmd.handle_operator_join(msg)

    async def _on_operator_command(msg: dict, c: Any) -> None:
        await cmd.execute_operator_command(c, msg.get("conversation_id", ""), msg.get("operator_id", "unknown"))

    async def _on_admin_command(msg: dict, c: Any) -> None:
        await cmd.execute_admin_command(c, msg.get("admin_id", "unknown"))

    async def _on_operator_message(msg: dict) -> None:
        await cmd.handle_operator_message(msg)

    async def _on_customer_message(msg: dict) -> None:
        await cmd.handle_customer_message(msg, msg_router)

    async def _on_customer_connect(msg: dict) -> None:
        await cmd.handle_customer_connect(msg, components.get("irc_transport"), components)

    async def _on_escalation(event: Event) -> None:
        await cmd.handle_escalation(event)

    async def _on_sla_breach(event: Event) -> None:
        await cmd.handle_sla_breach(event)

    bridge_server.on_operator_join = _on_operator_join
    bridge_server.on_operator_command = _on_operator_command
    bridge_server.on_admin_command = _on_admin_command
    bridge_server.on_operator_message = _on_operator_message
    bridge_server.on_customer_message = _on_customer_message
    bridge_server.on_customer_connect = _on_customer_connect

    event_bus: EventBus = components["event_bus"]
    event_bus.subscribe(EventType.TIMER_EXPIRED, _on_escalation)
    event_bus.subscribe(EventType.TIMER_EXPIRED, _on_sla_breach)


def build_components() -> dict[str, Any]:
    """组装所有 engine / bridge / transport 组件。不启动 IRC / WebSocket。"""
    from pathlib import Path as _Path
    from engine.db import init_db

    conn = init_db(CS_DB_PATH)
    eb = EventBus(conn)
    cm = ConversationManager(conn)
    channels = [c.strip() for c in IRC_CHANNELS.split(",") if c.strip()]
    return {
        "event_bus": eb,
        "conversation_manager": cm,
        "mode_manager": ModeManager(eb),
        "timer_manager": TimerManager(eb),
        "participant_registry": ParticipantRegistry(),
        "squad_registry": SquadRegistry(),
        "message_store": MessageStore(conn),
        "bridge_server": BridgeAPIServer(conversation_manager=cm, port=BRIDGE_PORT, host=BRIDGE_HOST),
        "irc_transport": IRCTransport(
            server=IRC_SERVER, port=IRC_PORT, nick=AGENT_NAME,
            channels=channels, tls=IRC_TLS, auth_token=IRC_AUTH_TOKEN,
        ),
        "routing_config": load_routing_config(CS_ROUTING_CONFIG),
        "plugin_manager": PluginManager(_Path(CS_PLUGINS_DIR)),
    }


async def main() -> None:
    """启动 channel-server：组件组装 → 回调注入 → IRC + Bridge 运行时。"""
    components = build_components()
    irc_transport: IRCTransport = components["irc_transport"]
    bridge_server: BridgeAPIServer = components["bridge_server"]
    conv_manager: ConversationManager = components["conversation_manager"]

    wire_bridge_callbacks(bridge_server, components, components.get("routing_config"))

    plugin_mgr = components.get("plugin_manager")
    if plugin_mgr and plugin_mgr.hook_names():
        print(f"[channel-server] plugins loaded: {sorted(plugin_mgr.hook_names())}", file=sys.stderr)
    else:
        print("[channel-server] WARNING: no plugins loaded", file=sys.stderr)

    await bridge_server.start()
    print(f"[channel-server] Bridge API listening on {BRIDGE_HOST}:{BRIDGE_PORT}", file=sys.stderr)

    # IRC → asyncio.Queue → MessageRouter → Bridge
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _on_pubmsg(conn, event):
        nick = event.source.nick
        if nick == AGENT_NAME:
            return
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"nick": nick, "channel": event.target, "body": event.arguments[0]},
        )

    def _on_privmsg(nick: str, body: str):
        conv_id = conv_manager.find_conversation_by_agent(nick)
        if conv_id is None:
            return
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"nick": nick, "channel": IRCTransport.conv_channel_name(conv_id), "body": body},
        )

    irc_transport.start(queue, loop, on_pubmsg=_on_pubmsg, on_privmsg_text=_on_privmsg)
    print(f"[channel-server] IRC bot '{AGENT_NAME}' connecting to {IRC_SERVER}:{IRC_PORT}", file=sys.stderr)

    main_msg_router = MessageRouter(
        conv_manager, components["message_store"], bridge_server, irc_transport,
    )

    async def _route_irc_messages() -> None:
        while True:
            msg = await queue.get()
            conv_id = IRCTransport.extract_conv_id(msg["channel"])
            if conv_id is not None:
                await main_msg_router.route_agent_message(msg["nick"], msg["body"], conv_id)

    shutdown = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    route_task = asyncio.create_task(_route_irc_messages())
    try:
        await shutdown.wait()
    finally:
        route_task.cancel()
        print("[channel-server] shutting down...", file=sys.stderr)
        irc_transport.disconnect("Server shutting down")
        await bridge_server.stop()
        components["event_bus"]._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        components["event_bus"].close()
        components["conversation_manager"].close_db()
        components["message_store"].close()


def entry_point() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    entry_point()
