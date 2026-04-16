"""CommandHandler — Operator / Admin 命令处理 (从 server.py 抽取)

集中管理 /hijack /release /copilot /resolve /abandon (operator) 和
/status /dispatch /review /assign /reassign /squad (admin) 命令的业务逻辑。

server.py 中 _on_operator_command / _on_admin_command 闭包代理到此类，
回调签名保持不变，保证 E2E 兼容。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Protocol

from zchat_protocol.commands import Command
from zchat_protocol.event import Event, EventType
from zchat_protocol.mode import ConversationMode
from zchat_protocol.participant import Participant, ParticipantRole

if TYPE_CHECKING:
    from engine.conversation_manager import ConversationManager
    from engine.event_bus import EventBus
    from engine.message_store import MessageStore
    from engine.squad_registry import SquadRegistry
    from engine.mode_manager import ModeManager
    from routing_config import RoutingConfig


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
    ) -> None:
        self._conv_manager = conv_manager
        self._mode_manager = mode_manager
        self._event_bus = event_bus
        self._message_store = message_store
        self._bridge = bridge_server
        self._squad_registry = squad_registry
        self._rc = routing_config

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

        if cmd.name == "abandon":
            await self._handle_abandon(conv_id, conv, operator_id)
            return

        if cmd.name == "resolve":
            await self._handle_resolve(conv_id, conv, operator_id)
            return

        # /hijack /release /copilot → 模式切换
        await self._handle_mode_switch(cmd, conv_id, conv, operator_id)

    # -- private operator handlers --

    async def _handle_abandon(
        self, conv_id: str, conv: Any, operator_id: str
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
        self, conv_id: str, conv: Any, operator_id: str
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
        if cmd.name == "status":
            await self._handle_status()
            return

        if cmd.name == "dispatch":
            await self._handle_dispatch(cmd, admin_id)
            return

        if cmd.name == "review":
            await self._handle_review()
            return

        if cmd.name == "assign":
            await self._handle_assign(cmd, admin_id)
            return

        if cmd.name == "reassign":
            await self._handle_reassign(cmd, admin_id)
            return

        if cmd.name == "squad":
            await self._handle_squad(cmd)
            return

    # -- private admin handlers --

    async def _handle_status(self) -> None:
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
        except Exception as e:
            print(f"[server] /dispatch failed: {e}", file=sys.stderr)

    async def _handle_review(self) -> None:
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

    async def _handle_squad(self, cmd: Command) -> None:
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
