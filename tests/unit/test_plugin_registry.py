"""PluginRegistry 单元测试。"""

from __future__ import annotations
import pytest
from channel_server.plugin import BasePlugin, Plugin, PluginRegistry


# ---- 测试用 Plugin 实现 ----

class SimplePlugin(BasePlugin):
    def __init__(self, name: str, commands: list[str] | None = None) -> None:
        self.name = name
        self._commands = commands or []
        self.received_messages: list[dict] = []
        self.received_events: list[dict] = []
        self.received_commands: list[tuple[str, dict]] = []

    def handles_commands(self) -> list[str]:
        return self._commands

    async def on_ws_message(self, msg: dict) -> None:
        self.received_messages.append(msg)

    async def on_ws_event(self, event: dict) -> None:
        self.received_events.append(event)

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        self.received_commands.append((cmd_name, msg))

    def query(self, key: str, args: dict | None = None):
        if key == "ping":
            return "pong"
        return None


class ErrorPlugin(BasePlugin):
    """on_ws_message / on_ws_event 总抛异常，用于测试 registry 容错。"""

    def __init__(self) -> None:
        self.name = "error-plugin"

    async def on_ws_message(self, msg: dict) -> None:
        raise RuntimeError("intentional error in on_ws_message")

    async def on_ws_event(self, event: dict) -> None:
        raise RuntimeError("intentional error in on_ws_event")


# ---- Tests ----

def test_register_plugin():
    registry = PluginRegistry()
    p = SimplePlugin("alpha")
    registry.register(p)
    assert registry.get_plugin("alpha") is p


def test_register_duplicate_plugin_raises():
    registry = PluginRegistry()
    p1 = SimplePlugin("alpha")
    p2 = SimplePlugin("alpha")
    registry.register(p1)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(p2)


def test_register_conflict_command_raises():
    registry = PluginRegistry()
    p1 = SimplePlugin("plugin-a", ["mode"])
    p2 = SimplePlugin("plugin-b", ["mode"])
    registry.register(p1)
    with pytest.raises(ValueError, match="already registered by"):
        registry.register(p2)


def test_get_handler_none_for_unregistered():
    registry = PluginRegistry()
    assert registry.get_handler("nonexistent") is None


def test_get_handler_returns_plugin_for_registered_command():
    registry = PluginRegistry()
    p = SimplePlugin("plugin-a", ["mode", "switch"])
    registry.register(p)
    assert registry.get_handler("mode") is p
    assert registry.get_handler("switch") is p


def test_all_plugins_returns_list():
    registry = PluginRegistry()
    p1 = SimplePlugin("alpha")
    p2 = SimplePlugin("beta", ["cmd"])
    registry.register(p1)
    registry.register(p2)
    plugins = registry.all_plugins()
    assert len(plugins) == 2
    names = {p.name for p in plugins}
    assert names == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_broadcast_message_to_all_plugins():
    registry = PluginRegistry()
    p1 = SimplePlugin("alpha")
    p2 = SimplePlugin("beta")
    registry.register(p1)
    registry.register(p2)

    msg = {"type": "message", "content": "hello"}
    await registry.broadcast_message(msg)

    assert p1.received_messages == [msg]
    assert p2.received_messages == [msg]


@pytest.mark.asyncio
async def test_broadcast_event_to_all_plugins():
    registry = PluginRegistry()
    p1 = SimplePlugin("alpha")
    p2 = SimplePlugin("beta")
    registry.register(p1)
    registry.register(p2)

    event = {"type": "event", "event": "agent_joined"}
    await registry.broadcast_event(event)

    assert p1.received_events == [event]
    assert p2.received_events == [event]


@pytest.mark.asyncio
async def test_plugin_error_does_not_break_broadcast_message():
    """一个 plugin 抛异常，其他 plugin 仍然能收到消息。"""
    registry = PluginRegistry()
    err_plugin = ErrorPlugin()
    good_plugin = SimplePlugin("good")
    registry.register(err_plugin)
    registry.register(good_plugin)

    msg = {"type": "message", "content": "test"}
    # 不应抛异常
    await registry.broadcast_message(msg)

    # good_plugin 仍然接收到消息
    assert good_plugin.received_messages == [msg]


@pytest.mark.asyncio
async def test_plugin_error_does_not_break_broadcast_event():
    """broadcast_event 中 plugin 抛异常不影响其他 plugin。"""
    registry = PluginRegistry()
    err_plugin = ErrorPlugin()
    good_plugin = SimplePlugin("good")
    registry.register(err_plugin)
    registry.register(good_plugin)

    event = {"type": "event", "event": "tick"}
    await registry.broadcast_event(event)

    assert good_plugin.received_events == [event]


def test_plugin_query():
    registry = PluginRegistry()
    p = SimplePlugin("alpha")
    registry.register(p)
    assert p.query("ping") == "pong"
    assert p.query("unknown") is None


def test_base_plugin_default_handles_commands():
    p = SimplePlugin("x")
    # 空 list（未指定命令）
    assert p._commands == []
    assert p.handles_commands() == []
