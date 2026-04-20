"""routing.toml 加载 + 查询 API（V6）。

zchat 系统唯一的运行时动态持久化。CLI 写入；CS 加载 + watch reload；bridge 读取映射。

V6 schema：

    [bots."customer"]
    app_id = "cli_..."
    credential_file = "credentials/customer.json"
    default_agent_template = "fast-agent"
    lazy_create_enabled = true

    [channels."conv-001"]
    bot = "customer"                  # 引用 [bots] name
    external_chat_id = "oc_..."
    entry_agent = "yaosh-fast-001"
    [channels."conv-001".agents]
    fast-001 = "yaosh-fast-001"
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
class Bot:
    name: str
    app_id: str
    credential_file: str | None = None
    default_agent_template: str | None = None
    lazy_create_enabled: bool = False


@dataclass
class ChannelRoute:
    channel_id: str
    bot: str | None = None                              # 引用 [bots] name
    external_chat_id: str | None = None                 # bridge 用（CS 不解析）
    entry_agent: str | None = None                      # router @ 谁（copilot 模式）
    agents: dict[str, str] = field(default_factory=dict)  # role → nick


@dataclass
class RoutingTable:
    channels: dict[str, ChannelRoute] = field(default_factory=dict)
    bots: dict[str, Bot] = field(default_factory=dict)

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

    def channels_for_bot(self, bot_name: str) -> list[ChannelRoute]:
        """返回某 bot 名下的所有 channel route。"""
        return [c for c in self.channels.values() if c.bot == bot_name]


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

    bots: dict[str, Bot] = {}
    for bot_name, b_data in (data.get("bots") or {}).items():
        if not isinstance(b_data, dict):
            continue
        bots[bot_name] = Bot(
            name=bot_name,
            app_id=b_data.get("app_id", ""),
            credential_file=b_data.get("credential_file"),
            default_agent_template=b_data.get("default_agent_template"),
            lazy_create_enabled=bool(b_data.get("lazy_create_enabled", False)),
        )

    channels: dict[str, ChannelRoute] = {}
    for ch_id, ch_data in (data.get("channels") or {}).items():
        if not isinstance(ch_data, dict):
            continue
        route = ChannelRoute(
            channel_id=ch_id,
            bot=ch_data.get("bot"),
            external_chat_id=ch_data.get("external_chat_id"),
            entry_agent=ch_data.get("entry_agent"),
            agents=dict(ch_data.get("agents") or {}),
        )
        channels[ch_id] = route

    return RoutingTable(channels=channels, bots=bots)
