"""MessageRouter — 消息路由逻辑提取 (spec §5 Transport)

从 server.py 提取的两条核心路由路径：
1. Customer → IRC: 客户消息 → 激活对话 → PRIVMSG 到已 dispatch 的 agent
2. Agent → Bridge: IRC agent 回复 → 解析前缀 → Bridge API 转发（统一 public）
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from zchat_protocol.conversation import ConversationState
from zchat_protocol.participant import Participant, ParticipantRole

from transport.irc_transport import IRCTransport, parse_agent_message

if TYPE_CHECKING:
    from bridge_api.ws_server import BridgeAPIServer
    from engine.conversation_manager import ConversationManager
    from engine.message_store import MessageStore


class MessageRouter:
    """消息路由器：负责 customer→IRC 和 agent→Bridge 两条路径的路由逻辑。"""

    def __init__(
        self,
        conv_manager: ConversationManager,
        message_store: MessageStore,
        bridge_server: BridgeAPIServer,
        irc_transport: IRCTransport | None = None,
    ) -> None:
        self._conv_manager = conv_manager
        self._message_store = message_store
        self._bridge_server = bridge_server
        self._irc_transport = irc_transport

    async def route_customer_message(
        self, conv_id: str, text: str, sender: str = "customer"
    ) -> None:
        """Customer message → activate conversation → PRIVMSG to agents → broadcast to bridges."""
        conv = self._conv_manager.get(conv_id)
        if conv is None:
            return

        # 激活对话（CREATED→ACTIVE 或 IDLE→ACTIVE）
        if conv.state == ConversationState.CREATED:
            self._conv_manager.activate(conv_id)
        elif conv.state == ConversationState.IDLE:
            self._conv_manager.activate(conv_id)

        if self._irc_transport is not None:
            # 发给 conversation channel（留存记录）
            channel = IRCTransport.conv_channel_name(conv_id)
            try:
                self._irc_transport.privmsg(channel, f"{sender}: {text}")
            except Exception:
                pass
            # 发给每个 dispatched agent 的 nick（agent 可能不在 #conv-xxx）
            for p in conv.participants:
                if p.role == ParticipantRole.AGENT:
                    try:
                        self._irc_transport.privmsg(
                            p.id,
                            f"[{conv_id}] {sender}: {text}",
                        )
                    except Exception as e:
                        print(
                            f"[server] PRIVMSG to {p.id} failed: {e}",
                            file=sys.stderr,
                        )

    async def _handle_edit(
        self, conv_id: str, parsed: dict, nick: str
    ) -> None:
        """处理 edit 类型消息。"""
        await self._bridge_server.send_edit(
            conv_id, parsed["message_id"], parsed["text"]
        )

    async def _handle_side(
        self, conv_id: str, parsed: dict, nick: str
    ) -> None:
        """处理 side 类型消息。"""
        await self._bridge_server.send_reply(
            conversation_id=conv_id,
            text=parsed["text"],
            visibility="side",
            sender_id=nick,
        )

    async def _handle_msg(
        self, conv_id: str, parsed: dict, nick: str
    ) -> None:
        """处理普通消息 — 统一以 public visibility 转发到 bridge（可见性判断由 bridge 负责）。"""
        await self._bridge_server.send_reply(
            conversation_id=conv_id,
            text=parsed["text"],
            visibility="public",
            message_id=parsed.get("message_id"),
            sender_id=nick,
        )

    _MSG_HANDLERS: dict[str, str] = {
        "edit": "_handle_edit",
        "side": "_handle_side",
    }

    async def route_agent_message(
        self, nick: str, body: str, conv_id: str
    ) -> None:
        """Agent reply from IRC → parse prefix → Gate → Bridge API send_reply."""
        parsed = parse_agent_message(body)
        try:
            handler_name = self._MSG_HANDLERS.get(parsed["type"])
            if handler_name is not None:
                handler = getattr(self, handler_name)
                await handler(conv_id, parsed, nick)
            else:
                await self._handle_msg(conv_id, parsed, nick)
        except Exception as e:
            print(f"[channel-server] route error: {e}", file=sys.stderr)
