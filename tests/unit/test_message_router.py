"""Unit 测试 engine/message_router.py — MessageRouter 路由逻辑验证。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.message_router import MessageRouter
from zchat_protocol.conversation import Conversation, ConversationState
from zchat_protocol.participant import Participant, ParticipantRole


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #


@pytest.fixture()
def conv_manager() -> MagicMock:
    """Mock ConversationManager。"""
    cm = MagicMock()
    cm.get.return_value = None
    cm.activate.return_value = None
    return cm


@pytest.fixture()
def message_store() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def bridge_server() -> AsyncMock:
    bs = AsyncMock()
    bs.send_reply = AsyncMock()
    bs.send_edit = AsyncMock()
    return bs


@pytest.fixture()
def irc_transport() -> MagicMock:
    t = MagicMock()
    t.privmsg = MagicMock()
    return t


@pytest.fixture()
def router(
    conv_manager: MagicMock,
    message_store: MagicMock,
    bridge_server: AsyncMock,
    irc_transport: MagicMock,
) -> MessageRouter:
    return MessageRouter(conv_manager, message_store, bridge_server, irc_transport)


def _make_conv(
    conv_id: str,
    state: ConversationState = ConversationState.ACTIVE,
    mode: str = "auto",
    participants: list[Participant] | None = None,
) -> Conversation:
    conv = Conversation(id=conv_id, state=state, mode=mode)
    if participants:
        conv.participants = participants
    return conv


# ------------------------------------------------------------------ #
# test_route_customer_message
# ------------------------------------------------------------------ #


def test_route_customer_message_activates_conversation(
    conv_manager: MagicMock, router: MessageRouter
) -> None:
    """CREATED 状态的 conversation 收到 customer 消息后应被激活。"""
    conv = _make_conv("c1", state=ConversationState.CREATED)
    conv_manager.get.return_value = conv

    asyncio.run(router.route_customer_message("c1", "hello"))

    conv_manager.activate.assert_called_once_with("c1")


def test_route_customer_message_activates_idle_conversation(
    conv_manager: MagicMock, router: MessageRouter
) -> None:
    """IDLE 状态的 conversation 收到 customer 消息后也应被激活。"""
    conv = _make_conv("c1", state=ConversationState.IDLE)
    conv_manager.get.return_value = conv

    asyncio.run(router.route_customer_message("c1", "hi"))

    conv_manager.activate.assert_called_once_with("c1")


def test_route_customer_message_sends_privmsg_to_agents(
    conv_manager: MagicMock,
    irc_transport: MagicMock,
    router: MessageRouter,
) -> None:
    """Customer 消息应通过 PRIVMSG 转发给所有 AGENT 参与者。"""
    agent1 = Participant(id="agent-a", role=ParticipantRole.AGENT)
    agent2 = Participant(id="agent-b", role=ParticipantRole.AGENT)
    operator = Participant(id="op1", role=ParticipantRole.OPERATOR)
    conv = _make_conv(
        "c1",
        state=ConversationState.ACTIVE,
        participants=[agent1, agent2, operator],
    )
    conv_manager.get.return_value = conv

    asyncio.run(router.route_customer_message("c1", "help me"))

    # channel 消息
    irc_transport.privmsg.assert_any_call("#conv-c1", "customer: help me")
    # agent PRIVMSG（每个 agent 一条）
    irc_transport.privmsg.assert_any_call("agent-a", "[c1] customer: help me")
    irc_transport.privmsg.assert_any_call("agent-b", "[c1] customer: help me")
    # operator 不应收到 PRIVMSG (总共 3 次调用: channel + 2 agents)
    assert irc_transport.privmsg.call_count == 3


def test_route_customer_message_no_irc_transport(
    conv_manager: MagicMock,
    message_store: MagicMock,
    bridge_server: AsyncMock,
) -> None:
    """没有 irc_transport 时不应报错，激活逻辑仍执行。"""
    router_no_irc = MessageRouter(conv_manager, message_store, bridge_server, None)
    conv = _make_conv("c1", state=ConversationState.CREATED)
    conv_manager.get.return_value = conv

    asyncio.run(router_no_irc.route_customer_message("c1", "test"))

    conv_manager.activate.assert_called_once_with("c1")


# ------------------------------------------------------------------ #
# test_route_agent_message
# ------------------------------------------------------------------ #


def test_route_agent_msg_public_sends_reply(
    conv_manager: MagicMock,
    bridge_server: AsyncMock,
    router: MessageRouter,
) -> None:
    """Auto 模式下 agent 普通消息应以 public visibility 发送。"""
    conv = _make_conv("c1", mode="auto")
    conv_manager.get.return_value = conv

    asyncio.run(router.route_agent_message("agent-a", "hello customer", "c1"))

    bridge_server.send_reply.assert_awaited_once_with(
        conversation_id="c1",
        text="hello customer",
        visibility="public",
        message_id=None,
        sender_id="agent-a",
    )


def test_route_agent_msg_side_sends_side(
    bridge_server: AsyncMock,
    router: MessageRouter,
) -> None:
    """__side: 前缀消息应以 side visibility 发送。"""
    asyncio.run(router.route_agent_message("agent-a", "__side:internal note", "c1"))

    bridge_server.send_reply.assert_awaited_once_with(
        conversation_id="c1",
        text="internal note",
        visibility="side",
        sender_id="agent-a",
    )


def test_route_agent_edit_sends_edit(
    bridge_server: AsyncMock,
    router: MessageRouter,
) -> None:
    """__edit: 前缀消息应调用 send_edit。"""
    asyncio.run(
        router.route_agent_message("agent-a", "__edit:msg-123:corrected text", "c1")
    )

    bridge_server.send_edit.assert_awaited_once_with("c1", "msg-123", "corrected text")


def test_route_agent_takeover_mode_still_public(
    conv_manager: MagicMock,
    bridge_server: AsyncMock,
    router: MessageRouter,
) -> None:
    """Takeover 模式下 agent 消息不再由 channel-server 降级，统一以 public visibility 转发。"""
    conv = _make_conv("c1", mode="takeover")
    conv_manager.get.return_value = conv

    asyncio.run(router.route_agent_message("agent-a", "reply to customer", "c1"))

    bridge_server.send_reply.assert_awaited_once_with(
        conversation_id="c1",
        text="reply to customer",
        visibility="public",
        message_id=None,
        sender_id="agent-a",
    )


def test_route_agent_msg_with_message_id(
    conv_manager: MagicMock,
    bridge_server: AsyncMock,
    router: MessageRouter,
) -> None:
    """__msg: 前缀消息应携带 message_id，Gate 仍适用。"""
    conv = _make_conv("c1", mode="auto")
    conv_manager.get.return_value = conv

    asyncio.run(
        router.route_agent_message("agent-a", "__msg:uuid-456:hello", "c1")
    )

    bridge_server.send_reply.assert_awaited_once_with(
        conversation_id="c1",
        text="hello",
        visibility="public",
        message_id="uuid-456",
        sender_id="agent-a",
    )


def test_route_agent_message_conv_not_found(
    conv_manager: MagicMock,
    bridge_server: AsyncMock,
    router: MessageRouter,
) -> None:
    """Conversation 不存在时，普通消息默认 public visibility。"""
    conv_manager.get.return_value = None

    asyncio.run(router.route_agent_message("agent-a", "message", "c-unknown"))

    bridge_server.send_reply.assert_awaited_once_with(
        conversation_id="c-unknown",
        text="message",
        visibility="public",
        message_id=None,
        sender_id="agent-a",
    )
