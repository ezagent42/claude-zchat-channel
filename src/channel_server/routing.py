"""routing.toml 加载 + 查询 API。

channel-server 私有配置文件。CLI 写入；server 加载；bridge 读取映射。
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

log = logging.getLogger(__name__)


@dataclass
class ChannelRoute:
    channel_id: str
    external_chat_id: str | None = None
    agents: dict[str, str] = field(default_factory=dict)  # role → nick


@dataclass
class RoutingTable:
    channels: dict[str, ChannelRoute] = field(default_factory=dict)

    def channel_agents(self, channel_id: str) -> list[str]:
        """返回某 channel 的所有 agent nick 列表（用于广播 @）。"""
        ch = self.channels.get(channel_id)
        return list(ch.agents.values()) if ch else []

    def external_chat_id(self, channel_id: str) -> str | None:
        """返回 channel 对应的 external_chat_id，未配置返回 None。"""
        ch = self.channels.get(channel_id)
        return ch.external_chat_id if ch else None


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
            external_chat_id=ch_data.get("external_chat_id"),
            agents=dict(ch_data.get("agents") or {}),
        )
        channels[ch_id] = route

    return RoutingTable(channels=channels)
