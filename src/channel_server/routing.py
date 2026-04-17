"""routing.toml 加载 + 查询 API。

channel-server 私有配置文件。CLI 写入；server 加载；bridge 通过 WS 查询。
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

log = logging.getLogger(__name__)


@dataclass
class ChannelRoute:
    channel_id: str
    feishu_chat_id: str | None = None
    squad_chat_id: str | None = None
    squad_thread_root: str | None = None
    default_agents: list[str] = field(default_factory=list)
    agents: dict[str, str] = field(default_factory=dict)  # role → nick


@dataclass
class RoutingTable:
    channels: dict[str, ChannelRoute] = field(default_factory=dict)
    operators: dict[str, dict[str, Any]] = field(default_factory=dict)

    def resolve_agent(self, channel_id: str, role: str) -> str | None:
        """channel + role → 实际 IRC nick。"""
        ch = self.channels.get(channel_id)
        return ch.agents.get(role) if ch else None

    def channel_agents(self, channel_id: str) -> list[str]:
        """返回某 channel 的所有 agent nick 列表（用于广播 @）。"""
        ch = self.channels.get(channel_id)
        return list(ch.agents.values()) if ch else []

    def identify_nick(self, nick: str) -> tuple[str | None, str | None]:
        """反查 nick → (role, channel_id)。找不到返回 (None, None)。"""
        for ch_id, route in self.channels.items():
            for role, n in route.agents.items():
                if n == nick:
                    return (role, ch_id)
        return (None, None)

    def feishu_mapping(self, channel_id: str) -> tuple[str | None, str | None, str | None]:
        """返回 (customer_chat, squad_chat, thread_root) 三元组。"""
        ch = self.channels.get(channel_id)
        if ch is None:
            return (None, None, None)
        return (ch.feishu_chat_id, ch.squad_chat_id, ch.squad_thread_root)


def load(path: str | Path) -> RoutingTable:
    """从 routing.toml 加载。文件不存在返回空表。"""
    p = Path(path)
    if not p.exists():
        log.info("routing config not found: %s — using empty table", p)
        return RoutingTable()
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        log.warning("failed to parse routing %s: %s — using empty", p, e)
        return RoutingTable()

    channels: dict[str, ChannelRoute] = {}
    for ch_id, ch_data in (data.get("channels") or {}).items():
        route = ChannelRoute(
            channel_id=ch_id,
            feishu_chat_id=ch_data.get("feishu_chat_id"),
            squad_chat_id=ch_data.get("squad_chat_id"),
            squad_thread_root=ch_data.get("squad_thread_root"),
            default_agents=list(ch_data.get("default_agents", [])),
            agents=dict(ch_data.get("agents") or {}),
        )
        channels[ch_id] = route

    operators = dict(data.get("operators") or {})
    return RoutingTable(channels=channels, operators=operators)
