#!/usr/bin/env python3
"""zchat-channel-server v1.0 — 独立进程入口.

独立运行的中心服务：IRC bot (cs-bot) + Bridge API :9999 + engine 组装。
不包含 MCP Server 代码（MCP 在 agent_mcp.py 中）。

IRC 侧通过 `transport.irc_transport.IRCTransport` 连接 ergo，
Bridge 侧通过 `bridge_api.ws_server.BridgeAPIServer` 暴露 WebSocket，
核心业务由 `engine/` 各组件承载，协议原语在 `protocol/`。
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
from zchat_protocol.conversation import ConversationState
from zchat_protocol.event import Event, EventType
from zchat_protocol.gate import gate_message
from zchat_protocol.message_types import MessageVisibility
from zchat_protocol.mode import ConversationMode
from zchat_protocol.participant import Participant, ParticipantRole
from routing_config import RoutingConfig, load_routing_config
from transport.irc_transport import IRCTransport

# ------------------------------------------------------------------ #
# 环境变量
# ------------------------------------------------------------------ #

AGENT_NAME = os.environ.get("AGENT_NAME", "cs-bot")
IRC_SERVER = os.environ.get("IRC_SERVER", "127.0.0.1")
IRC_PORT = int(os.environ.get("IRC_PORT", "6667"))
IRC_CHANNELS = os.environ.get("IRC_CHANNELS", "general")
IRC_TLS = os.environ.get("IRC_TLS", "false").lower() == "true"
IRC_AUTH_TOKEN = os.environ.get("IRC_AUTH_TOKEN", "")

BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "9999"))
BRIDGE_HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")

CS_DB_PATH = os.environ.get("CS_DB_PATH", "conversations.db")
CS_ROUTING_CONFIG = os.environ.get("CS_ROUTING_CONFIG", "routing.toml")
CS_PLUGINS_DIR = os.environ.get(
    "CS_PLUGINS_DIR", str(__import__("pathlib").Path(__file__).parent / "plugins")
)


# ------------------------------------------------------------------ #
# Bridge 回调注入
# ------------------------------------------------------------------ #


def wire_bridge_callbacks(
    bridge_server: BridgeAPIServer,
    components: dict[str, Any],
    routing_config: RoutingConfig | None = None,
) -> None:
    """将业务回调注入到 BridgeAPIServer 的钩子槽中。

    从 main() 中独立出来便于单元测试。

    线程安全说明: ConversationManager/MessageStore 的同步 SQLite 操作在 asyncio
    协程内调用是安全的 — asyncio 是协作式调度，SQLite 操作是瞬时 CPU-bound，
    不会在操作中间被 await 中断。check_same_thread=False 仅因为 Connection 在
    build_components() 中创建，在此处使用。
    """
    conv_manager: ConversationManager = components["conversation_manager"]
    mode_manager: ModeManager = components["mode_manager"]
    rc = routing_config or RoutingConfig()

    # CommandHandler 承载 operator / admin 命令业务逻辑
    cmd_handler = CommandHandler(
        conv_manager=conv_manager,
        mode_manager=mode_manager,
        event_bus=components["event_bus"],
        message_store=components["message_store"],
        bridge_server=bridge_server,
        squad_registry=components["squad_registry"],
        routing_config=rc,
    )

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
        """Operator 发送命令（/hijack / /release / /copilot / /resolve / /abandon）→ CommandHandler。"""
        conv_id = msg.get("conversation_id", "")
        operator_id = msg.get("operator_id", "unknown")
        await cmd_handler.execute_operator_command(cmd, conv_id, operator_id)

    async def _on_admin_command(msg: dict, cmd: Any) -> None:
        """Admin 发送命令（/status / /dispatch / /review / /assign / /reassign / /squad）→ CommandHandler。"""
        admin_id = msg.get("admin_id", "unknown")
        await cmd_handler.execute_admin_command(cmd, admin_id)

    async def _on_operator_message(msg: dict) -> None:
        """Operator 通过 Bridge API 发消息 → Gate 判定 visibility → 转发。"""
        conv_id = msg.get("conversation_id", "")
        operator_id = msg.get("operator_id", "unknown")
        text = msg.get("text", "")

        conv = conv_manager.get(conv_id)
        if conv is None:
            print(f"[server] operator_message: conversation {conv_id!r} not found", file=sys.stderr)
            return

        operator_participant = Participant(id=operator_id, role=ParticipantRole.OPERATOR)
        gate_result = gate_message(conv, operator_participant, MessageVisibility.PUBLIC)
        visibility = gate_result.value
        message_store: MessageStore = components["message_store"]
        saved = message_store.save(
            conversation_id=conv_id,
            source=operator_id,
            content=text,
            visibility=visibility,
        )
        await bridge_server.send_reply(
            conversation_id=conv_id,
            text=text,
            visibility=visibility,
            message_id=saved.id,
        )

    msg_router = MessageRouter(
        conv_manager, components.get("message_store"), bridge_server,
        irc_transport=components.get("irc_transport"),
    )

    async def _on_customer_message(msg: dict) -> None:
        """Customer 消息处理: 转发到 IRC 给 agent + CSAT 评分接收。"""
        conv_id = msg.get("conversation_id", "")
        csat_score = msg.get("csat_score")
        if csat_score is not None:
            try:
                conv_manager.set_csat(conv_id, int(csat_score))
            except Exception as e:
                print(f"[server] set_csat failed: {e}", file=sys.stderr)
            return

        text = msg.get("text", "")
        if text and conv_id:
            await msg_router.route_customer_message(conv_id, text)

    async def _on_customer_connect(msg: dict) -> None:
        """Customer 接入 → 创建 conversation + IRC bot JOIN + auto-dispatch。"""
        conv_id = msg.get("conversation_id", "")
        if not conv_id:
            return
        # 创建 conversation（幂等）
        metadata = dict(msg.get("metadata", {}))
        customer = msg.get("customer")
        if customer is not None:
            metadata["customer"] = customer
        conv = conv_manager.create(conv_id, metadata=metadata)

        # IRC bot auto-JOIN
        irc_transport = components.get("irc_transport")
        if irc_transport is not None:
            channel = IRCTransport.conv_channel_name(conv_id)
            try:
                irc_transport.join(channel)
            except Exception as e:
                print(f"[server] auto-join {channel} failed: {e}", file=sys.stderr)

        # auto-dispatch default_agents
        for agent_nick in rc.default_agents:
            try:
                participant = Participant(id=agent_nick, role=ParticipantRole.AGENT)
                conv_manager.add_participant(conv_id, participant)
                await bridge_server.send_event(
                    "agent.dispatched",
                    {"agent_nick": agent_nick, "dispatched_by": "__auto"},
                    conv_id,
                )
            except Exception as e:
                print(f"[server] auto-dispatch {agent_nick} failed: {e}", file=sys.stderr)
        # App plugin hook: sla_onboard 等 App 层 timer 在此处设置
        plugin_manager: PluginManager = components["plugin_manager"]
        await plugin_manager.fire(
            "on_conversation_created",
            conv_id=conv_id,
            components=components,
        )

    async def _on_escalation(event: Event) -> None:
        """Escalation event → 按 escalation_chain 顺序 dispatch 到第一个可用 agent。"""
        conv_id = event.conversation_id
        if not conv_id or not rc.escalation_chain:
            return
        conv = conv_manager.get(conv_id)
        if conv is None:
            return
        existing_ids = {p.id for p in (conv.participants or [])}
        for target in rc.escalation_chain:
            if target == "operator":
                # 发告警通知 admin 介入
                await bridge_server.send_reply(
                    conversation_id=conv_id,
                    text=f"[escalation] 需要人工介入: {conv_id}",
                    visibility="system",
                )
                return
            if target in existing_ids:
                continue
            try:
                participant = Participant(id=target, role=ParticipantRole.AGENT)
                conv_manager.add_participant(conv_id, participant)
                await bridge_server.send_event(
                    "agent.dispatched",
                    {"agent_nick": target, "dispatched_by": "__escalation"},
                    conv_id,
                )
                return
            except Exception as e:
                print(f"[server] escalation dispatch {target} failed: {e}", file=sys.stderr)

    # 注入回调
    bridge_server.on_operator_join = _on_operator_join
    bridge_server.on_operator_command = _on_operator_command
    bridge_server.on_admin_command = _on_admin_command
    bridge_server.on_operator_message = _on_operator_message
    bridge_server.on_customer_message = _on_customer_message
    bridge_server.on_customer_connect = _on_customer_connect

    async def _on_sla_breach(event: Event) -> None:
        """SLA timer 超时 → 向 admin 发送告警。"""
        timer_name = event.data.get("name", "")
        if not timer_name.startswith("sla_"):
            return
        conv_id = event.conversation_id
        duration = event.data.get("action_params", {}).get("duration_s", "?")
        await bridge_server.send_event(
            "sla.breach",
            {
                "conversation_id": conv_id,
                "breach_type": timer_name,
                "timeout_seconds": duration,
            },
            conv_id,
            target_capabilities={"operator", "admin"},
        )
        await bridge_server.send_reply(
            conversation_id="__admin",
            text=f"[SLA 告警] conv_id={conv_id} breach={timer_name} timeout={duration}s",
            visibility="system",
        )

    # EventBus 订阅
    event_bus: EventBus = components["event_bus"]
    event_bus.subscribe(EventType.TIMER_EXPIRED, _on_escalation)
    event_bus.subscribe(EventType.TIMER_EXPIRED, _on_sla_breach)


# ------------------------------------------------------------------ #
# 组件组装
# ------------------------------------------------------------------ #


def build_components() -> dict[str, Any]:
    """组装所有 engine / bridge / transport 组件。不启动 IRC / WebSocket。"""
    from engine.db import init_db

    routing_cfg = load_routing_config(CS_ROUTING_CONFIG)
    conn = init_db(CS_DB_PATH)
    event_bus = EventBus(conn)
    conversation_manager = ConversationManager(conn)
    mode_manager = ModeManager(event_bus)
    timer_manager = TimerManager(event_bus)
    participant_registry = ParticipantRegistry()
    squad_registry = SquadRegistry()
    message_store = MessageStore(conn)
    from pathlib import Path as _Path
    plugin_manager = PluginManager(_Path(CS_PLUGINS_DIR))
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
        "squad_registry": squad_registry,
        "message_store": message_store,
        "bridge_server": bridge_server,
        "irc_transport": irc_transport,
        "routing_config": routing_cfg,
        "plugin_manager": plugin_manager,
    }


# ------------------------------------------------------------------ #
# main — 独立进程
# ------------------------------------------------------------------ #


async def main() -> None:
    """启动 channel-server 独立进程：IRC bot + Bridge API + engine。"""
    components = build_components()
    irc_transport: IRCTransport = components["irc_transport"]
    bridge_server: BridgeAPIServer = components["bridge_server"]

    wire_bridge_callbacks(bridge_server, components, components.get("routing_config"))

    # 组件就绪检查
    plugin_mgr = components.get("plugin_manager")
    if plugin_mgr and plugin_mgr.hook_names():
        print(f"[channel-server] plugins loaded: {sorted(plugin_mgr.hook_names())}", file=sys.stderr)
    else:
        print("[channel-server] WARNING: no plugins loaded", file=sys.stderr)

    # 启动 Bridge API WebSocket
    await bridge_server.start()
    print(
        f"[channel-server] Bridge API listening on {BRIDGE_HOST}:{BRIDGE_PORT}",
        file=sys.stderr,
    )

    # 启动 IRC bot + 消息路由
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    conv_manager: ConversationManager = components["conversation_manager"]
    message_store: MessageStore = components["message_store"]

    def _on_pubmsg(conn, event):
        """IRC 频道消息 → 入队待路由到 Bridge API。"""
        nick = event.source.nick
        if nick == AGENT_NAME:
            return
        channel = event.target
        body = event.arguments[0]
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"nick": nick, "channel": channel, "body": body},
        )

    def _on_privmsg(nick: str, body: str):
        """Agent 回复 cs-bot 的 PRIVMSG → 入队待路由。"""
        print(f"[server] PRIVMSG from {nick}: {body[:80]}", file=sys.stderr)
        print(f"[server] conversations: {list(conv_manager._conversations.keys())}", file=sys.stderr)
        # 从 agent 的 participant 记录中找到其参与的 conversation
        conv_id = None
        for cid, conv in conv_manager._conversations.items():
            for p in conv.participants:
                if p.id == nick and p.role == ParticipantRole.AGENT:
                    conv_id = cid
                    break
            if conv_id:
                break
        if conv_id is None:
            return
        channel = IRCTransport.conv_channel_name(conv_id)
        loop.call_soon_threadsafe(
            queue.put_nowait,
            {"nick": nick, "channel": channel, "body": body},
        )

    irc_transport.start(queue, loop, on_pubmsg=_on_pubmsg, on_privmsg_text=_on_privmsg)
    print(
        f"[channel-server] IRC bot '{AGENT_NAME}' connecting to {IRC_SERVER}:{IRC_PORT}",
        file=sys.stderr,
    )

    # MessageRouter 用于 main() 的 IRC→Bridge 路由
    main_msg_router = MessageRouter(
        conv_manager, message_store, bridge_server, irc_transport
    )

    async def _route_irc_messages() -> None:
        """从 IRC 队列读取 agent 消息，解析前缀后路由到 Bridge API。"""
        while True:
            msg = await queue.get()
            channel = msg["channel"]
            conv_id = IRCTransport.extract_conv_id(channel)
            if conv_id is None:
                continue
            await main_msg_router.route_agent_message(
                msg["nick"], msg["body"], conv_id
            )

    # 等待 shutdown 信号
    shutdown = asyncio.Event()

    def _on_signal() -> None:
        shutdown.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_signal)

    # 启动 IRC 消息路由任务
    route_task = asyncio.create_task(_route_irc_messages())

    try:
        await shutdown.wait()
    finally:
        route_task.cancel()
        print("[channel-server] shutting down...", file=sys.stderr)
        irc_transport.disconnect("Server shutting down")
        await bridge_server.stop()
        # WAL checkpoint 确保所有写入持久化
        conn = components["event_bus"]._conn
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        components["event_bus"].close()
        components["conversation_manager"].close_db()
        components["message_store"].close()


def entry_point() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    entry_point()
