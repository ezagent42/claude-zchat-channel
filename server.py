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
from engine.conversation_manager import ConversationManager
from engine.event_bus import EventBus
from engine.message_store import MessageStore
from engine.mode_manager import ModeManager
from engine.participant_registry import ParticipantRegistry
from engine.timer_manager import TimerManager
from protocol.event import Event, EventType
from protocol.gate import gate_message
from protocol.message_types import MessageVisibility
from protocol.mode import ConversationMode
from protocol.participant import Participant, ParticipantRole
from routing_config import RoutingConfig, load_routing_config
from transport.irc_transport import IRCTransport, parse_agent_message

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
CS_EVENT_DB_PATH = os.environ.get(
    "CS_EVENT_DB_PATH", CS_DB_PATH.replace(".db", "_events.db")
)
CS_MESSAGE_DB_PATH = os.environ.get(
    "CS_MESSAGE_DB_PATH", CS_DB_PATH.replace(".db", "_messages.db")
)
CS_ROUTING_CONFIG = os.environ.get("CS_ROUTING_CONFIG", "routing.toml")


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
    """
    conv_manager: ConversationManager = components["conversation_manager"]
    mode_manager: ModeManager = components["mode_manager"]
    rc = routing_config or RoutingConfig()

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
        """Operator 发送命令（/hijack / /release / /copilot / /resolve）→ 模式切换。"""
        conv_id = msg.get("conversation_id", "")
        operator_id = msg.get("operator_id", "unknown")

        conv = conv_manager.get(conv_id)
        if conv is None:
            return

        # /resolve → 结案 + CSAT 流程
        if cmd.name == "resolve":
            try:
                # CREATED 状态需要先激活才能 close
                if conv.state.value == "created":
                    conv_manager.activate(conv_id)
                conv_manager.resolve(conv_id, outcome="resolved", resolved_by=operator_id)
                await bridge_server.send_event(
                    "conversation.resolved",
                    {"outcome": "resolved", "resolved_by": operator_id},
                    conv_id,
                )
                await bridge_server.send_reply(
                    conversation_id=conv_id,
                    text="[system] 对话已结案，请评分 1-5",
                    visibility="public",
                )
            except Exception as e:
                print(f"[server] /resolve failed: {e}", file=sys.stderr)
            return

        # /hijack /release /copilot → 模式切换
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

    async def _on_admin_command(msg: dict, cmd: Any) -> None:
        """Admin 发送命令（/status / /dispatch）。"""
        admin_id = msg.get("admin_id", "unknown")

        if cmd.name == "status":
            convs = conv_manager.list_active()
            if not convs:
                text = "[status] 无活跃对话 (0)"
            else:
                lines = [f"[status] 活跃对话 ({len(convs)}):"]
                for c in convs:
                    p_count = len(c.participants) if c.participants else 0
                    lines.append(f"  {c.id} | {c.state.value} | {c.mode} | {p_count}人")
                text = "\n".join(lines)
            await bridge_server.send_reply(
                conversation_id="__admin",
                text=text,
                visibility="system",
            )
            return

        if cmd.name == "dispatch":
            target_conv_id = cmd.args.get("conversation_id", "")
            agent_nick = cmd.args.get("agent_nick", "")
            conv = conv_manager.get(target_conv_id)
            if conv is None:
                return
            # 白名单验证
            if not rc.is_dispatch_allowed(agent_nick):
                await bridge_server.send_reply(
                    conversation_id="__admin",
                    text=f"[dispatch] rejected: {agent_nick} not in available_agents",
                    visibility="system",
                )
                return
            try:
                participant = Participant(id=agent_nick, role=ParticipantRole.AGENT)
                conv_manager.add_participant(target_conv_id, participant)
                await bridge_server.send_event(
                    "agent.dispatched",
                    {"agent_nick": agent_nick, "dispatched_by": admin_id},
                    target_conv_id,
                )
            except Exception as e:
                print(f"[server] /dispatch failed: {e}", file=sys.stderr)
            return

    async def _on_customer_message(msg: dict) -> None:
        """Customer 消息处理（含 CSAT 评分接收）。"""
        conv_id = msg.get("conversation_id", "")
        csat_score = msg.get("csat_score")
        if csat_score is not None:
            try:
                conv_manager.set_csat(conv_id, int(csat_score))
            except Exception as e:
                print(f"[server] set_csat failed: {e}", file=sys.stderr)

    async def _on_customer_connect(msg: dict) -> None:
        """Customer 接入 → IRC bot JOIN 对应 #conv-{id} 频道 + auto-dispatch。"""
        conv_id = msg.get("conversation_id", "")
        if not conv_id:
            return
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
    bridge_server.on_customer_message = _on_customer_message
    bridge_server.on_customer_connect = _on_customer_connect

    # EventBus 订阅: escalation
    event_bus: EventBus = components["event_bus"]
    event_bus.subscribe(EventType.TIMER_EXPIRED, _on_escalation)


# ------------------------------------------------------------------ #
# 组件组装
# ------------------------------------------------------------------ #


def build_components() -> dict[str, Any]:
    """组装所有 engine / bridge / transport 组件。不启动 IRC / WebSocket。"""
    routing_cfg = load_routing_config(CS_ROUTING_CONFIG)
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
        "routing_config": routing_cfg,
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

    irc_transport.start(queue, loop, on_pubmsg=_on_pubmsg)
    print(
        f"[channel-server] IRC bot '{AGENT_NAME}' connecting to {IRC_SERVER}:{IRC_PORT}",
        file=sys.stderr,
    )

    async def _route_irc_messages() -> None:
        """从 IRC 队列读取 agent 消息，解析前缀后路由到 Bridge API。"""
        while True:
            msg = await queue.get()
            channel = msg["channel"]
            conv_id = IRCTransport.extract_conv_id(channel)
            if conv_id is None:
                continue

            parsed = parse_agent_message(msg["body"])
            try:
                if parsed["type"] == "edit":
                    await bridge_server.send_edit(
                        conv_id, parsed["message_id"], parsed["text"]
                    )
                elif parsed["type"] == "side":
                    await bridge_server.send_reply(
                        conversation_id=conv_id,
                        text=parsed["text"],
                        visibility="side",
                    )
                else:
                    # 普通消息 — Gate 根据 mode + role 判定 visibility
                    conv = conv_manager.get(conv_id)
                    visibility = "public"
                    if conv is not None:
                        agent_participant = Participant(
                            id=msg["nick"], role=ParticipantRole.AGENT
                        )
                        gate_result = gate_message(
                            conv, agent_participant, MessageVisibility.PUBLIC
                        )
                        visibility = gate_result.value
                    await bridge_server.send_reply(
                        conversation_id=conv_id,
                        text=parsed["text"],
                        visibility=visibility,
                        message_id=parsed.get("message_id"),
                    )
            except Exception as e:
                print(f"[channel-server] route error: {e}", file=sys.stderr)

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
        components["event_bus"].close()
        components["conversation_manager"].close_db()
        components["message_store"].close()


def entry_point() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    entry_point()
