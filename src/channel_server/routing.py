"""routing.toml 加载 + 查询 API。

zchat 系统唯一的运行时动态持久化。CLI 写入；CS 加载 + watch reload；bridge 读取映射。
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
    external_chat_id: str | None = None           # bridge 用（CS 不解析）
    bot_id: str | None = None                      # bridge 用（CS 不解析，过滤属于自己的 channel）
    entry_agent: str | None = None                 # router @ 谁（copilot 模式）
    agents: dict[str, str] = field(default_factory=dict)  # role → nick（辅助查询）


@dataclass
class RoutingTable:
    channels: dict[str, ChannelRoute] = field(default_factory=dict)

    def channel_agents(self, channel_id: str) -> list[str]:
        """返回某 channel 的所有 agent nick 列表。"""
        ch = self.channels.get(channel_id)
        return list(ch.agents.values()) if ch else []

    def entry_agent(self, channel_id: str) -> str | None:
        """返回 channel 的入口 agent nick（copilot 模式下被 @ 的唯一 agent）。"""
        ch = self.channels.get(channel_id)
        return ch.entry_agent if ch else None

    def external_chat_id(self, channel_id: str) -> str | None:
        """返回 channel 对应的 external_chat_id。"""
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
            bot_id=ch_data.get("bot_id"),
            entry_agent=ch_data.get("entry_agent"),
            agents=dict(ch_data.get("agents") or {}),
        )
        channels[ch_id] = route

    return RoutingTable(channels=channels)
