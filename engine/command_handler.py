"""CommandHandler — Operator / Admin / Bridge 回调业务逻辑 (从 server.py 抽取)

集中管理:
- operator 命令: /hijack /release /copilot /resolve /abandon
- admin 命令: /status /dispatch /review /assign /reassign /squad
- bridge 回调: operator_join, customer_connect, escalation

server.py 中闭包代理到此类，回调签名保持不变，保证 E2E 兼容。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Protocol

from zchat_protocol.commands import Command
from zchat_protocol.event import Event, EventType
from zchat_protocol.gate import gate_message
from zchat_protocol.message_types import MessageVisibility
from zchat_protocol.mode import ConversationMode
from zchat_protocol.participant import Participant, ParticipantRole
from zchat_protocol.sys_messages import encode_sys_for_irc, make_sys_message

if TYPE_CHECKING:
    from engine.conversation_manager import ConversationManager
    from engine.event_bus import EventBus
    from engine.message_store import MessageStore
    from engine.squad_registry import SquadRegistry
    from engine.mode_manager import ModeManager
    from routing_config import RoutingConfig
    from transport.irc_transport import IRCTransport


class BridgeReply(Protocol):
    """BridgeAPIServer 需要实现的回复接口（duck typing）。"""

    async def send_event(
        self,
        event_type: str,
        data: dict[str, Any],
        conversation_id: str,
        **kwargs: Any,
    ) -> None: ...

    async def send_reply(
        self,
        *,
        conversation_id: str,
        text: str,
        visibility: str,
        message_id: str | None = None,
        sender_id: str | None = None,
    ) -> None: ...


class CommandHandler:
    """无状态命令处理器 — 接收命令，执行业务逻辑，通过 bridge 发出回复/事件。"""

    def __init__(
        self,
        conv_manager: ConversationManager,
        mode_manager: ModeManager,
        event_bus: EventBus,
        message_store: MessageStore,
        bridge_server: BridgeReply,
        squad_registry: SquadRegistry,
        routing_config: RoutingConfig,
        irc_transport: IRCTransport | None = None,
    ) -> None:
        self._conv_manager = conv_manager
        self._mode_manager = mode_manager
        self._event_bus = event_bus
        self._message_store = message_store
        self._bridge = bridge_server
        self._squad_registry = squad_registry
        self._rc = routing_config
        self._irc_transport = irc_transport

        # 命令分发表（声明式，不硬编码）
        self._operator_commands: dict[str, Any] = {
            "abandon": self._handle_abandon,
            "resolve": self._handle_resolve,
            "hijack": self._handle_mode_switch,
            "release": self._handle_mode_switch,
            "copilot": self._handle_mode_switch,
        }
        self._admin_commands: dict[str, Any] = {
            "status": self._handle_status,
            "dispatch": self._handle_dispatch,
            "review": self._handle_review,
            "assign": self._handle_assign,
            "reassign": self._handle_reassign,
            "squad": self._handle_squad,
        }

    # ------------------------------------------------------------------ #
    # IRC sys.join_request helper
    # ------------------------------------------------------------------ #

    def _send_join_request(self, conv_id: str, agent_nick: str) -> None:
        """通过 IRC PRIVMSG 向 agent 发送 sys.join_request，让其 JOIN 对话频道。"""
        if self._irc_transport is None:
            return
        from transport.irc_transport import IRCTransport

        channel = IRCTransport.conv_channel_name(conv_id)
        sys_msg = make_sys_message("cs-bot", "sys.join_request", {"channel": channel})
        try:
            self._irc_transport.privmsg(agent_nick, encode_sys_for_irc(sys_msg))
        except Exception as e:
            print(f"[server] sys.join_request to {agent_nick} failed: {e}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # Bridge 回调 — operator_join / customer_connect / escalation
    # ------------------------------------------------------------------ #

    async def handle_operator_join(self, msg: dict) -> None:
        """Operator 通过 Bridge 加入对话 → 注册参与者 + 触发模式切换。"""
        conv_id = msg.get("conversation_id", "")
        operator = msg.get("operator", {})
        operator_id = operator.get("id", "unknown")

        conv = self._conv_manager.get(conv_id)
        if conv is None:
            print(
                f"[server] operator_join: conversation {conv_id!r} not found",
                file=sys.stderr,
            )
            return

        # 注册 operator 参与者
        participant = Participant(id=operator_id, role=ParticipantRole.OPERATOR)
        try:
            self._conv_manager.add_participant(conv_id, participant)
        except Exception as e:
            print(f"[server] add_participant failed: {e}", file=sys.stderr)

        # 模式切换：auto → copilot（仅当前为 auto 时）
        if conv.mode == ConversationMode.AUTO.value:
            try:
                t = await self._mode_manager.atransition(
                    conv,
                    ConversationMode.COPILOT,
                    trigger="operator_join",
                    triggered_by=operator_id,
                )
                await self._bridge.send_event(
                    "mode.changed",
                    {"from": t.from_mode.value, "to": t.to_mode.value,
                     "trigger": "operator_join", "triggered_by": operator_id},
                    conv_id,
                )
            except Exception as e:
                print(f"[server] mode transition failed: {e}", file=sys.stderr)

    async def handle_customer_connect(
        self,
        msg: dict,
        irc_transport: Any | None,
        components: dict[str, Any],
    ) -> None:
        """Customer 接入 → 创建 conversation + IRC bot JOIN + auto-dispatch。

        ``components`` 按引用传入，以便 plugin_manager 等可在运行时被替换
        （测试场景会在 wire_bridge_callbacks 之后替换 mock）。
        """
        from transport.irc_transport import IRCTransport

        conv_id = msg.get("conversation_id", "")
        if not conv_id:
            return
        print(f"[server] customer_connect: {conv_id}", file=sys.stderr)
        # 创建 conversation（幂等）
        metadata = dict(msg.get("metadata", {}))
        customer = msg.get("customer")
        if customer is not None:
            metadata["customer"] = customer
        self._conv_manager.create(conv_id, metadata=metadata)

        # IRC bot auto-JOIN
        if irc_transport is not None:
            channel = IRCTransport.conv_channel_name(conv_id)
            print(f"[server] joining {channel}", file=sys.stderr)
            try:
                irc_transport.join(channel)
                print(f"[server] joined {channel}", file=sys.stderr)
            except Exception as e:
                print(f"[server] auto-join {channel} failed: {e}", file=sys.stderr)
        else:
            print("[server] WARNING: no irc_transport, skipping channel join", file=sys.stderr)

        # auto-dispatch default_agents
        for agent_nick in self._rc.default_agents:
            try:
                participant = Participant(id=agent_nick, role=ParticipantRole.AGENT)
                self._conv_manager.add_participant(conv_id, participant)
                await self._bridge.send_event(
                    "agent.dispatched",
                    {"agent_nick": agent_nick, "dispatched_by": "__auto"},
                    conv_id,
                )
                self._send_join_request(conv_id, agent_nick)
            except Exception as e:
                print(f"[server] auto-dispatch {agent_nick} failed: {e}", file=sys.stderr)

        # App plugin hook: sla_onboard 等 App 层 timer 在此处设置
        plugin_manager = components["plugin_manager"]
        await plugin_manager.fire(
            "on_conversation_created",
            conv_id=conv_id,
            components=components,
        )

    async def handle_operator_message(self, msg: dict) -> None:
        """Operator 通过 Bridge API 发消息 → Gate 判定 visibility → 存储 + 转发。

        当 visibility=side（copilot 模式下 operator 建议）时，
        额外通过 IRC @mention 注入给 conversation 中的 agent（PRD 旅程 3）。
        """
        conv_id = msg.get("conversation_id", "")
        conv = self._conv_manager.get(conv_id)
        if conv is None:
            print(f"[server] operator_message: conversation {conv_id!r} not found", file=sys.stderr)
            return
        operator_id = msg.get("operator_id", "unknown")
        text = msg.get("text", "")
        participant = Participant(id=operator_id, role=ParticipantRole.OPERATOR)
        visibility = gate_message(conv, participant, MessageVisibility.PUBLIC).value
        saved = self._message_store.save(
            conversation_id=conv_id, source=operator_id, content=text, visibility=visibility,
        )
        await self._bridge.send_reply(
            conversation_id=conv_id, text=text, visibility=visibility, message_id=saved.id,
        )

        # T5: side 消息注入给 agent — 走 IRC #conv-{id} @mention
        if visibility == "side" and self._irc_transport is not None:
            from transport.irc_transport import IRCTransport
            channel = IRCTransport.conv_channel_name(conv_id)
            for p in conv.participants:
                if p.role == ParticipantRole.AGENT:
                    try:
                        self._irc_transport.privmsg(
                            channel,
                            f"@{p.id} __side:{operator_id}: {text}",
                        )
                    except Exception as e:
                        print(f"[server] side inject to {p.id} failed: {e}", file=sys.stderr)

    async def handle_customer_message(self, msg: dict, msg_router: Any) -> None:
        """Customer 消息: 转发到 IRC + CSAT 评分接收。"""
        conv_id = msg.get("conversation_id", "")
        csat_score = msg.get("csat_score")
        if csat_score is not None:
            try:
                self._conv_manager.set_csat(conv_id, int(csat_score))
            except Exception as e:
                print(f"[server] set_csat failed: {e}", file=sys.stderr)
            return
        text = msg.get("text", "")
        if text and conv_id:
            await msg_router.route_customer_message(conv_id, text)

    async def handle_sla_breach(self, event: "Event") -> None:
        """SLA timer 超时 → 向 admin 发送告警。"""
        timer_name = event.data.get("name", "")
        if not timer_name.startswith("sla_"):
            return
        conv_id = event.conversation_id
        duration = event.data.get("action_params", {}).get("duration_s", "?")
        await self._bridge.send_event(
            "sla.breach",
            {"conversation_id": conv_id, "breach_type": timer_name, "timeout_seconds": duration},
            conv_id,
            target_capabilities={"operator", "admin"},
        )
        await self._bridge.send_reply(
            conversation_id="__admin",
            text=f"[SLA 告警] conv_id={conv_id} breach={timer_name} timeout={duration}s",
            visibility="system",
        )

    async def handle_escalation(self, event: "Event") -> None:
        """Escalation event → 按 escalation_chain 顺序 dispatch 到第一个可用 agent。"""
        conv_id = event.conversation_id
        if not conv_id or not self._rc.escalation_chain:
            return
        conv = self._conv_manager.get(conv_id)
        if conv is None:
            return
        existing_ids = {p.id for p in (conv.participants or [])}
        for target in self._rc.escalation_chain:
            if target == "operator":
                # 发告警通知 admin 介入
                await self._bridge.send_reply(
                    conversation_id=conv_id,
                    text=f"[escalation] 需要人工介入: {conv_id}",
                    visibility="system",
                )
                return
            if target in existing_ids:
                continue
            try:
                participant = Participant(id=target, role=ParticipantRole.AGENT)
                self._conv_manager.add_participant(conv_id, participant)
                await self._bridge.send_event(
                    "agent.dispatched",
                    {"agent_nick": target, "dispatched_by": "__escalation"},
                    conv_id,
                )
                self._send_join_request(conv_id, target)
                return
            except Exception as e:
                print(f"[server] escalation dispatch {target} failed: {e}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # Operator 命令
    # ------------------------------------------------------------------ #

    async def execute_operator_command(
        self, cmd: Command, conv_id: str, operator_id: str
    ) -> None:
        """执行 operator 命令：/hijack /release /copilot /resolve /abandon。

        conv_id 对应的 conversation 不存在时静默返回。
        """
        conv = self._conv_manager.get(conv_id)
        if conv is None:
            return

        handler = self._operator_commands.get(cmd.name)
        if handler:
            await handler(cmd, conv_id, conv, operator_id)

    # -- private operator handlers --

    async def _handle_abandon(
        self, cmd: Command, conv_id: str, conv: Any, operator_id: str
    ) -> None:
        """/abandon → 直接关闭对话（不发 CSAT，不标 outcome）。"""
        try:
            if conv.state.value == "created":
                self._conv_manager.activate(conv_id)
            self._conv_manager.close(conv_id)
            await self._event_bus.publish(
                Event(
                    type=EventType.CONVERSATION_CLOSED,
                    conversation_id=conv_id,
                    data={"abandoned_by": operator_id},
                )
            )
            await self._bridge.send_event(
                "conversation.closed",
                {"abandoned_by": operator_id, "trigger": "abandon"},
                conv_id,
            )
            await self._bridge.send_reply(
                conversation_id=conv_id,
                text=f"[system] 对话已被 {operator_id} 放弃",
                visibility="system",
            )
        except Exception as e:
            print(f"[server] /abandon failed: {e}", file=sys.stderr)

    async def _handle_resolve(
        self, cmd: Command, conv_id: str, conv: Any, operator_id: str
    ) -> None:
        """/resolve → 结案 + CSAT 流程。"""
        try:
            # CREATED 状态需要先激活才能 close
            if conv.state.value == "created":
                self._conv_manager.activate(conv_id)
            self._conv_manager.resolve(
                conv_id, outcome="resolved", resolved_by=operator_id
            )
            await self._bridge.send_event(
                "conversation.resolved",
                {"outcome": "resolved", "resolved_by": operator_id},
                conv_id,
            )
            await self._bridge.send_reply(
                conversation_id=conv_id,
                text="[system] 对话已结案，请评分 1-5",
                visibility="public",
            )
        except Exception as e:
            print(f"[server] /resolve failed: {e}", file=sys.stderr)

    async def _handle_mode_switch(
        self, cmd: Command, conv_id: str, conv: Any, operator_id: str
    ) -> None:
        """/hijack /release /copilot → 模式切换。"""
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
            t = await self._mode_manager.atransition(
                conv,
                target_mode,
                trigger=cmd.name,
                triggered_by=operator_id,
            )
            await self._bridge.send_event(
                "mode.changed",
                {
                    "from": t.from_mode.value,
                    "to": t.to_mode.value,
                    "trigger": cmd.name,
                    "triggered_by": operator_id,
                },
                conv_id,
            )
            # hijack 后发出 side visibility 系统通知（E2E gate enforcement 验证路径）
            if cmd.name == "hijack":
                await self._bridge.send_reply(
                    conversation_id=conv_id,
                    text=f"[system] takeover activated by {operator_id}",
                    visibility="side",
                )
        except Exception as e:
            print(f"[server] command {cmd.name} failed: {e}", file=sys.stderr)

    # ------------------------------------------------------------------ #
    # Admin 命令
    # ------------------------------------------------------------------ #

    async def execute_admin_command(self, cmd: Command, admin_id: str) -> None:
        """执行 admin 命令：/status /dispatch /review /assign /reassign /squad。"""
        handler = self._admin_commands.get(cmd.name)
        if handler:
            await handler(cmd, admin_id)

    # -- private admin handlers --

    async def _handle_status(self, cmd: Command, admin_id: str) -> None:
        """/status → 列出活跃对话。"""
        convs = self._conv_manager.list_active()
        if not convs:
            text = "[status] 无活跃对话 (0)"
        else:
            lines = [f"[status] 活跃对话 ({len(convs)}):"]
            for c in convs:
                p_count = len(c.participants) if c.participants else 0
                lines.append(
                    f"  {c.id} | {c.state.value} | {c.mode} | {p_count}人"
                )
            text = "\n".join(lines)
        await self._bridge.send_reply(
            conversation_id="__admin",
            text=text,
            visibility="system",
        )

    async def _handle_dispatch(self, cmd: Command, admin_id: str) -> None:
        """/dispatch → 分派 agent 到 conversation。"""
        target_conv_id = cmd.args.get("conversation_id", "")
        agent_nick = cmd.args.get("agent_nick", "")
        conv = self._conv_manager.get(target_conv_id)
        if conv is None:
            return
        # 白名单验证
        if not self._rc.is_dispatch_allowed(agent_nick):
            await self._bridge.send_reply(
                conversation_id="__admin",
                text=f"[dispatch] rejected: {agent_nick} not in available_agents",
                visibility="system",
            )
            return
        try:
            participant = Participant(id=agent_nick, role=ParticipantRole.AGENT)
            self._conv_manager.add_participant(target_conv_id, participant)
            await self._bridge.send_event(
                "agent.dispatched",
                {"agent_nick": agent_nick, "dispatched_by": admin_id},
                target_conv_id,
            )
            self._send_join_request(target_conv_id, agent_nick)
        except Exception as e:
            print(f"[server] /dispatch failed: {e}", file=sys.stderr)

    async def _handle_review(self, cmd: Command, admin_id: str) -> None:
        """/review → 聚合统计。"""
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        # 聚合统计：对话数来自 ConversationManager，其余来自 EventBus
        all_convs = list(self._conv_manager._conversations.values())
        conv_count = len(all_convs)
        all_events = self._event_bus.query(since=yesterday)
        takeover_count = sum(
            1
            for e in all_events
            if e.type == EventType.MODE_CHANGED
            and e.data.get("to") == "takeover"
        )
        resolved_count = sum(
            1 for c in all_convs if c.state.value == "closed"
        )
        csat_scores = [
            c.resolution.csat_score
            for c in all_convs
            if c.resolution is not None and c.resolution.csat_score is not None
        ]
        csat_avg = (
            sum(csat_scores) / len(csat_scores) if csat_scores else 0.0
        )
        resolve_rate = (
            round(resolved_count / conv_count * 100, 1)
            if conv_count > 0
            else 0.0
        )

        if conv_count == 0:
            text = "[review] 暂无统计数据（过去 24h 无对话）"
        else:
            text = (
                f"[review] 过去 24h 统计:\n"
                f"  对话数: {conv_count}\n"
                f"  接管次数: {takeover_count}\n"
                f"  结案率: {resolve_rate}%\n"
                f"  CSAT 均分: {csat_avg:.1f}"
            )
        await self._bridge.send_reply(
            conversation_id="__admin",
            text=text,
            visibility="system",
        )

    async def _handle_assign(self, cmd: Command, admin_id: str) -> None:
        """/assign → 添加 agent→operator 映射。"""
        agent_nick = cmd.args.get("agent_nick", "")
        operator_id = cmd.args.get("operator_id", "")
        if not agent_nick or not operator_id:
            await self._bridge.send_reply(
                conversation_id="__admin",
                text="[assign] usage: /assign <agent_nick> <operator_id>",
                visibility="system",
            )
            return
        try:
            self._squad_registry.assign(agent_nick, operator_id)
            await self._event_bus.publish(
                Event(
                    type=EventType.SQUAD_ASSIGNED,
                    conversation_id="",
                    data={
                        "agent_nick": agent_nick,
                        "operator_id": operator_id,
                        "assigned_by": admin_id,
                    },
                )
            )
            await self._bridge.send_event(
                "squad.assigned",
                {
                    "agent_nick": agent_nick,
                    "operator_id": operator_id,
                    "assigned_by": admin_id,
                },
                "__admin",
            )
            await self._bridge.send_reply(
                conversation_id="__admin",
                text=f"[assign] {agent_nick} → {operator_id}",
                visibility="system",
            )
        except Exception as e:
            print(f"[server] /assign failed: {e}", file=sys.stderr)

    async def _handle_reassign(self, cmd: Command, admin_id: str) -> None:
        """/reassign → 显式 from→to 迁移。"""
        agent_nick = cmd.args.get("agent_nick", "")
        from_op = cmd.args.get("from_operator", "")
        to_op = cmd.args.get("to_operator", "")
        if not agent_nick or not to_op:
            await self._bridge.send_reply(
                conversation_id="__admin",
                text="[reassign] usage: /reassign <agent_nick> <from_op> <to_op>",
                visibility="system",
            )
            return
        try:
            self._squad_registry.reassign(agent_nick, to_op)
            await self._event_bus.publish(
                Event(
                    type=EventType.SQUAD_REASSIGNED,
                    conversation_id="",
                    data={
                        "agent_nick": agent_nick,
                        "from_operator": from_op,
                        "to_operator": to_op,
                        "reassigned_by": admin_id,
                    },
                )
            )
            await self._bridge.send_event(
                "squad.reassigned",
                {
                    "agent_nick": agent_nick,
                    "from_operator": from_op,
                    "to_operator": to_op,
                    "reassigned_by": admin_id,
                },
                "__admin",
            )
            await self._bridge.send_reply(
                conversation_id="__admin",
                text=f"[reassign] {agent_nick}: {from_op} → {to_op}",
                visibility="system",
            )
        except Exception as e:
            print(f"[server] /reassign failed: {e}", file=sys.stderr)

    async def _handle_squad(self, cmd: Command, admin_id: str) -> None:
        """/squad → 列出分队信息。"""
        target = cmd.args.get("target", "")
        if target:
            agents = self._squad_registry.get_squad(target)
            if agents:
                text = f"[squad] {target}: {', '.join(agents)}"
            else:
                text = f"[squad] {target}: 暂无 agent"
        else:
            squads = self._squad_registry.list_all()
            if not squads:
                text = "[squad] 暂无分队"
            else:
                lines = ["[squad] 全部分队:"]
                for op_id in sorted(squads.keys()):
                    agents = squads[op_id]
                    lines.append(f"  {op_id}: {', '.join(agents)}")
                text = "\n".join(lines)
        await self._bridge.send_reply(
            conversation_id="__admin",
            text=text,
            visibility="system",
        )
