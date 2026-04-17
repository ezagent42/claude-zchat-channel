"""Plugin 框架。

Plugin 通过 handles_commands() 自声明接管哪些命令（以 "/" 开头的消息 content）。
Plugin 通过 on_ws_message / on_ws_event 订阅事件。
PluginRegistry 管理插件集合 + 命令→插件的查询表，启动时冲突检测。
"""

from __future__ import annotations
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Plugin(Protocol):
    """Plugin 接口 — 官方 plugin 实现此协议。"""

    name: str

    def handles_commands(self) -> list[str]:
        """声明接管的命令名（不含 "/" 前缀）。默认空。"""
        return []

    async def on_ws_message(self, msg: dict) -> None:
        """订阅所有 WS message 类消息。默认 no-op。"""
        ...

    async def on_ws_event(self, event: dict) -> None:
        """订阅 ws event。默认 no-op。"""
        ...

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        """处理自己声明的命令。"""
        ...

    def query(self, key: str, args: dict | None = None) -> Any:
        """其他组件（router/其他 plugin）查询此 plugin 状态。默认 None。"""
        return None


class BasePlugin:
    """Plugin 基类 — 提供所有方法的空默认实现，plugin 作者继承此类。"""

    name: str = ""

    def handles_commands(self) -> list[str]:
        return []

    async def on_ws_message(self, msg: dict) -> None:
        pass

    async def on_ws_event(self, event: dict) -> None:
        pass

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        pass

    def query(self, key: str, args: dict | None = None) -> Any:
        return None


class PluginRegistry:
    """维护 plugin 集合 + command→plugin 分派表 + event 订阅列表。"""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._command_handlers: dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        """注册 plugin。handles_commands 冲突时抛 ValueError（启动即失败，不延迟到运行时）。"""
        if plugin.name in self._plugins:
            raise ValueError(f"plugin {plugin.name!r} already registered")
        for cmd in plugin.handles_commands():
            if cmd in self._command_handlers:
                existing = self._command_handlers[cmd].name
                raise ValueError(
                    f"command {cmd!r} claimed by {plugin.name!r} but already registered by {existing!r}"
                )
        self._plugins[plugin.name] = plugin
        for cmd in plugin.handles_commands():
            self._command_handlers[cmd] = plugin

    def get_handler(self, cmd_name: str) -> Plugin | None:
        return self._command_handlers.get(cmd_name)

    def get_plugin(self, name: str) -> Plugin | None:
        return self._plugins.get(name)

    def all_plugins(self) -> list[Plugin]:
        return list(self._plugins.values())

    async def broadcast_message(self, msg: dict) -> None:
        """给所有 plugin 转发 message（on_ws_message）。"""
        for p in self._plugins.values():
            try:
                await p.on_ws_message(msg)
            except Exception as e:
                import sys
                print(f"[plugin {p.name}] on_ws_message error: {e}", file=sys.stderr)

    async def broadcast_event(self, event: dict) -> None:
        """给所有 plugin 转发 event（on_ws_event）。"""
        for p in self._plugins.values():
            try:
                await p.on_ws_event(event)
            except Exception as e:
                import sys
                print(f"[plugin {p.name}] on_ws_event error: {e}", file=sys.stderr)
