"""Router 单元测试 — 用 mock IRCConnection + mock WSServer。"""

from __future__ import annotations
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from channel_server.plugin import BasePlugin, PluginRegistry
from channel_server.router import Router
from channel_server.routing import ChannelRoute, RoutingTable
from zchat_protocol import irc_encoding, ws_messages


# ---- Mock 帮助类 ----

class MockIRCConnection:
    """记录 privmsg 调用，不实际连接 IRC。

    可选 _members 字典 → 用于 router NAMES 熔断测试（将 channel 映射到 member nick set）。
    """

    def __init__(self, members: dict[str, set[str]] | None = None):
        self.sent: list[tuple[str, str]] = []  # (target, content)
        self._members = members or {}

    def privmsg(self, target: str, content: str) -> None:
        self.sent.append((target, content))

    def names(self, channel: str) -> set[str]:
        ch = channel if channel.startswith("#") else f"#{channel}"
        return set(self._members.get(ch, set()))


class MockWSServer:
    """记录 broadcast 调用，不实际发送 WS。"""

    def __init__(self):
        self.broadcasts: list[dict] = []

    async def broadcast(self, msg: dict) -> None:
        self.broadcasts.append(msg)


class ModePlugin(BasePlugin):
    """模拟 mode plugin，支持 query("get", {"channel": ...}) → mode str。"""

    def __init__(self, default_mode: str = "copilot") -> None:
        self.name = "mode"
        self._modes: dict[str, str] = {}
        self._default = default_mode

    def set_mode(self, channel: str, mode: str) -> None:
        self._modes[channel] = mode

    def query(self, key: str, args: dict | None = None) -> Any:
        if key == "get" and args:
            return self._modes.get(args.get("channel", ""), self._default)
        return None


class CommandPlugin(BasePlugin):
    """记录 on_command 调用。"""

    def __init__(self, name: str, commands: list[str]) -> None:
        self.name = name
        self._commands = commands
        self.received: list[tuple[str, dict]] = []

    def handles_commands(self) -> list[str]:
        return self._commands

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        self.received.append((cmd_name, msg))


# ---- Fixtures ----

def make_routing_with_entry(
    channel_id: str = "general",
    entry_agent: str | None = "yaosh-fast-001",
) -> RoutingTable:
    route = ChannelRoute(entry_agent=entry_agent)
    return RoutingTable(channels={channel_id: route})


def make_router(
    routing: RoutingTable | None = None,
    registry: PluginRegistry | None = None,
    irc_conn: MockIRCConnection | None = None,
    ws_server: MockWSServer | None = None,
) -> tuple[Router, MockIRCConnection, MockWSServer]:
    routing = routing or RoutingTable()
    registry = registry or PluginRegistry()
    irc_conn = irc_conn or MockIRCConnection()
    ws_server = ws_server or MockWSServer()
    router = Router(routing=routing, registry=registry, irc_conn=irc_conn, ws_server=ws_server)
    return router, irc_conn, ws_server


# ---- Tests ----

@pytest.mark.asyncio
async def test_default_mode_when_no_mode_plugin():
    """无 mode plugin 时，Router 使用默认 copilot mode。"""
    router, _, _ = make_router()
    assert router._query_mode("any-channel") == Router.DEFAULT_MODE


@pytest.mark.asyncio
async def test_message_without_command_routes_to_irc_with_at_prefix_in_copilot():
    """普通消息 + copilot mode → 向 entry_agent @ 发送。"""
    routing = make_routing_with_entry("general", "yaosh-fast-001")
    registry = PluginRegistry()
    mode_plugin = ModePlugin("copilot")
    registry.register(mode_plugin)

    router, irc_conn, ws_server = make_router(routing=routing, registry=registry)

    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "general",
        "content": "hello world",
        "message_id": "test-uuid-001",
    }
    await router.forward_inbound_ws(msg)

    assert len(irc_conn.sent) == 1
    target, content = irc_conn.sent[0]
    assert target == "#general"
    assert content.startswith("@yaosh-fast-001")
    # 确认包含 __msg: 前缀
    assert irc_encoding.MSG_PREFIX in content


@pytest.mark.asyncio
async def test_message_without_command_routes_to_irc_without_prefix_in_takeover():
    """takeover mode → 直接发 IRC，不 @ prefix。"""
    routing = make_routing_with_entry("general", "yaosh-fast-001")
    registry = PluginRegistry()
    mode_plugin = ModePlugin("takeover")
    registry.register(mode_plugin)

    router, irc_conn, ws_server = make_router(routing=routing, registry=registry)

    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "general",
        "content": "hello world",
        "message_id": "test-uuid-002",
    }
    await router.forward_inbound_ws(msg)

    assert len(irc_conn.sent) == 1
    target, content = irc_conn.sent[0]
    assert target == "#general"
    # 不应以 @ 开头
    assert not content.startswith("@yaosh-fast-001")
    # 仍然包含 __msg: 前缀（encode_msg 自动加）
    assert irc_encoding.MSG_PREFIX in content


@pytest.mark.asyncio
async def test_message_with_infra_command_goes_to_plugin_not_irc():
    """以 '/' 开头且有注册 plugin 的命令 → plugin 处理，不发 IRC。"""
    routing = make_routing_with_entry("general")
    registry = PluginRegistry()
    cmd_plugin = CommandPlugin("mode", ["mode"])
    registry.register(cmd_plugin)

    router, irc_conn, ws_server = make_router(routing=routing, registry=registry)

    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "general",
        "content": "/mode copilot",
    }
    await router.forward_inbound_ws(msg)

    # plugin 收到命令
    assert len(cmd_plugin.received) == 1
    assert cmd_plugin.received[0][0] == "mode"

    # IRC 没有收到任何消息
    assert irc_conn.sent == []


@pytest.mark.asyncio
async def test_message_with_unknown_command_routes_to_irc():
    """/unknown 命令无 plugin 注册 → 当普通消息转发到 IRC。"""
    routing = make_routing_with_entry("general", "yaosh-fast-001")
    registry = PluginRegistry()
    # 不注册任何 plugin

    router, irc_conn, ws_server = make_router(routing=routing, registry=registry)

    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "general",
        "content": "/unknown some args",
        "message_id": "test-uuid-003",
    }
    await router.forward_inbound_ws(msg)

    # 因为默认 copilot mode，应发送
    assert len(irc_conn.sent) == 1


@pytest.mark.asyncio
async def test_irc_inbound_becomes_ws_broadcast():
    """IRC pubmsg → 广播给 WS bridges。"""
    router, irc_conn, ws_server = make_router()

    await router.forward_inbound_irc("#general", "yaosh-agent-001", "hello from irc")

    assert len(ws_server.broadcasts) == 1
    broadcast = ws_server.broadcasts[0]
    assert broadcast["type"] == ws_messages.WSType.MESSAGE
    assert broadcast["channel"] == "general"
    assert broadcast["source"] == "yaosh-agent-001"
    assert broadcast["content"] == "hello from irc"


@pytest.mark.asyncio
async def test_irc_inbound_strips_hash_prefix():
    """IRC 频道名 #general → WS channel 字段为 general（不含 #）。"""
    router, _, ws_server = make_router()
    await router.forward_inbound_irc("#my-channel", "bot", "msg")
    assert ws_server.broadcasts[0]["channel"] == "my-channel"


@pytest.mark.asyncio
async def test_irc_inbound_with_msg_prefix_extracts_message_id():
    """IRC 消息带 __msg: 前缀 → WS 消息含 message_id。"""
    router, _, ws_server = make_router()
    encoded = irc_encoding.encode_msg("uuid-abc", "some text")
    await router.forward_inbound_irc("#general", "bot", encoded)

    broadcast = ws_server.broadcasts[0]
    assert broadcast.get("message_id") == "uuid-abc"


@pytest.mark.asyncio
async def test_message_also_broadcast_to_plugins():
    """bridge → router → plugins 也收到消息。"""
    routing = RoutingTable()  # 空路由
    registry = PluginRegistry()

    class RecordPlugin(BasePlugin):
        def __init__(self):
            self.name = "recorder"
            self.msgs = []

        async def on_ws_message(self, msg):
            self.msgs.append(msg)

    recorder = RecordPlugin()
    registry.register(recorder)

    router, _, _ = make_router(routing=routing, registry=registry)

    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "",  # 空 channel → 不发 IRC，但仍 broadcast to plugins
        "content": "test",
    }
    await router.forward_inbound_ws(msg)
    assert len(recorder.msgs) == 1


@pytest.mark.asyncio
async def test_emit_event_broadcasts_to_ws_and_plugins():
    """emit_event → WS broadcast + plugin on_ws_event。"""
    routing = RoutingTable()
    registry = PluginRegistry()

    class EventRecorder(BasePlugin):
        def __init__(self):
            self.name = "ev-recorder"
            self.events = []

        async def on_ws_event(self, event):
            self.events.append(event)

    recorder = EventRecorder()
    registry.register(recorder)

    router, _, ws_server = make_router(routing=routing, registry=registry)
    await router.emit_event("general", "agent_joined", {"nick": "bot"})

    assert len(ws_server.broadcasts) == 1
    assert ws_server.broadcasts[0]["event"] == "agent_joined"
    assert len(recorder.events) == 1
    assert recorder.events[0]["event"] == "agent_joined"


@pytest.mark.asyncio
async def test_copilot_mode_only_ats_entry_agent():
    """copilot mode → router 只 @ entry_agent。"""
    routing = make_routing_with_entry("general", entry_agent="yaosh-fast-001")
    registry = PluginRegistry()

    router, irc_conn, _ = make_router(routing=routing, registry=registry)

    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "general",
        "content": "question for all",
        "message_id": "mid-999",
    }
    await router.forward_inbound_ws(msg)

    # 只发一条，目标是 entry_agent
    assert len(irc_conn.sent) == 1
    target, content = irc_conn.sent[0]
    assert target == "#general"
    assert content.startswith("@yaosh-fast-001 ")


@pytest.mark.asyncio
async def test_emit_event_sends_irc_sys_msg():
    """emit_event 除 WS 广播外，也发 IRC __zchat_sys: 到 channel。"""
    router, irc_conn, ws_server = make_router()
    await router.emit_event("general", "mode_changed", {"from": "copilot", "to": "takeover"})

    # WS broadcast 有
    assert len(ws_server.broadcasts) == 1
    assert ws_server.broadcasts[0]["event"] == "mode_changed"

    # IRC sys 消息有，发到 #general
    assert len(irc_conn.sent) == 1
    target, content = irc_conn.sent[0]
    assert target == "#general"
    assert content.startswith("__zchat_sys:")
    # sys payload 能被 parse
    parsed = irc_encoding.parse(content)
    assert parsed["kind"] == "sys"
    assert parsed["payload"]["type"] == "mode_changed"


@pytest.mark.asyncio
async def test_emit_event_no_channel_skips_irc():
    """channel 为空时 emit_event 不发 IRC。"""
    router, irc_conn, ws_server = make_router()
    await router.emit_event("", "global_event", {"key": "val"})

    assert len(ws_server.broadcasts) == 1
    assert len(irc_conn.sent) == 0


@pytest.mark.asyncio
async def test_emit_event_truncates_long_text_for_irc():
    """长 text 字段（中文 200+ bytes）不能让 IRC sys payload 超 512 字节。WS 路径不截。"""
    router, irc_conn, ws_server = make_router()
    long_text = "中" * 300  # 300 字符 × 3 bytes/utf-8 = 900 bytes
    await router.emit_event("c1", "help_requested",
                             {"text": long_text, "channel": "c1"})

    # WS broadcast 保留 full text
    assert len(ws_server.broadcasts) == 1
    assert ws_server.broadcasts[0]["data"]["text"] == long_text
    # IRC sys payload encoded 字节数不超过 IRC PRIVMSG 512 limit（含协议头 ~50 byte 富余）
    assert len(irc_conn.sent) == 1
    _, irc_content = irc_conn.sent[0]
    assert len(irc_content.encode("utf-8")) <= 462  # 512 - 50 (IRC prefix headroom)
    # 截断标记 "…" 在 IRC 版本里
    assert "…" in irc_content


@pytest.mark.asyncio
async def test_copilot_mode_entry_offline_emits_help_requested():
    """copilot mode + entry_agent 不在 IRC NAMES → 不空 @，emit help_requested。"""
    routing = make_routing_with_entry("general", entry_agent="alice-fast")
    registry = PluginRegistry()
    # IRC channel 里只有 cs-bot，alice-fast 不在
    irc_conn = MockIRCConnection(members={"#general": {"cs-bot"}})
    router, _, _ = make_router(routing=routing, registry=registry, irc_conn=irc_conn)

    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "general",
        "content": "anyone home",
    }
    await router.forward_inbound_ws(msg)

    has_at = any(c.startswith("@") for _, c in irc_conn.sent)
    assert not has_at
    sys_events = [c for _, c in irc_conn.sent if "help_requested" in c]
    assert len(sys_events) == 1
    assert "entry_offline" in sys_events[0]


@pytest.mark.asyncio
async def test_copilot_mode_entry_present_does_not_short_circuit():
    """copilot mode + entry_agent 在 IRC NAMES → 正常 @，不 emit help_requested。"""
    routing = make_routing_with_entry("general", entry_agent="alice-fast")
    registry = PluginRegistry()
    irc_conn = MockIRCConnection(members={"#general": {"cs-bot", "alice-fast"}})
    router, _, _ = make_router(routing=routing, registry=registry, irc_conn=irc_conn)

    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "general",
        "content": "hi",
    }
    await router.forward_inbound_ws(msg)

    at_lines = [c for _, c in irc_conn.sent if c.startswith("@alice-fast")]
    assert len(at_lines) == 1
    sys_events = [c for _, c in irc_conn.sent if "help_requested" in c]
    assert len(sys_events) == 0


@pytest.mark.asyncio
async def test_copilot_mode_without_entry_agent_emits_help_requested():
    """copilot mode 但 channel 无 entry_agent → 不 @ 任何人，emit help_requested 系统事件。"""
    route = ChannelRoute(entry_agent=None)
    routing = RoutingTable(channels={"orphan": route})
    registry = PluginRegistry()
    router, irc_conn, _ = make_router(routing=routing, registry=registry)

    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "orphan",
        "content": "hi",
    }
    await router.forward_inbound_ws(msg)

    # 没 @ 任何 agent
    has_at = any(content.startswith("@") for _, content in irc_conn.sent)
    assert not has_at, "should not @ any agent when no entry_agent"
    # 应有 1 条 help_requested 系统事件
    sys_events = [c for _, c in irc_conn.sent if "help_requested" in c]
    assert len(sys_events) == 1
    assert "no_entry_agent" in sys_events[0]


@pytest.mark.asyncio
async def test_already_encoded_content_not_double_encoded():
    """content 已带 __msg: 前缀 → router 不再重复 encode。"""
    routing = make_routing_with_entry("general", "yaosh-fast-001")
    registry = PluginRegistry()

    router, irc_conn, _ = make_router(routing=routing, registry=registry)

    pre_encoded = irc_encoding.encode_msg("existing-id", "already encoded text")
    msg = {
        "type": ws_messages.WSType.MESSAGE,
        "channel": "general",
        "content": pre_encoded,
    }
    await router.forward_inbound_ws(msg)

    _, content = irc_conn.sent[0]
    # content 应该包含 __msg: 但只出现一次（不被双重编码）
    assert content.count(irc_encoding.MSG_PREFIX) == 1


@pytest.mark.asyncio
async def test_irc_inbound_command_dispatches_to_plugin():
    """IRC 侧发 /hijack → plugin on_command 被调用。"""
    registry = PluginRegistry()
    cmd_plugin = CommandPlugin("mode", ["hijack", "release"])
    registry.register(cmd_plugin)

    router, irc_conn, ws_server = make_router(registry=registry)
    await router.forward_inbound_irc("#general", "yaosh", "/hijack")

    assert len(cmd_plugin.received) == 1
    assert cmd_plugin.received[0][0] == "hijack"
    # 命令被 plugin 消费，不广播给 bridge
    assert len(ws_server.broadcasts) == 0


@pytest.mark.asyncio
async def test_irc_inbound_unknown_command_broadcasts_normally():
    """IRC 侧发 /unknown → 无 plugin 处理 → 当普通消息广播。"""
    router, _, ws_server = make_router()
    await router.forward_inbound_irc("#general", "yaosh", "/unknown stuff")

    assert len(ws_server.broadcasts) == 1
    assert ws_server.broadcasts[0]["content"] == "/unknown stuff"
