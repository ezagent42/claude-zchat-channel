"""routing.toml 加载 + 查询 API（V7）。

zchat 系统唯一的运行时动态持久化。CLI 写入；CS 加载 + watch reload；bridge 读取映射。

V7 schema（V7 起 credential_file 是 app_id 的唯一来源；routing.toml 不再写 app_id）：

    [bots."<bot_name>"]
    credential_file = "credentials/<bot_name>.json"   # 必填（含 app_id + app_secret）
    default_agent_template = "fast-agent"
    lazy_create_enabled = true

    [channels."conv-001"]
    bot = "<bot_name>"                # 引用 [bots] name
    external_chat_id = "oc_..."
    entry_agent = "yaosh-fast-001"    # router @ 谁；roster 由 IRC NAMES 反映
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
    credential_file: str | None = None
    default_agent_template: str | None = None
    lazy_create_enabled: bool = False


@dataclass
class ChannelRoute:
    bot: str | None = None                              # 引用 [bots] name
    external_chat_id: str | None = None                 # bridge 用（CS 不解析）
    entry_agent: str | None = None                      # router @ 谁（copilot 模式）


@dataclass
class RoutingTable:
    channels: dict[str, ChannelRoute] = field(default_factory=dict)
    bots: dict[str, Bot] = field(default_factory=dict)

    def entry_agent(self, channel_id: str) -> str | None:
        """返回 channel 的入口 agent nick（copilot 模式下被 @ 的唯一 agent）。"""
        ch = self.channels.get(channel_id)
        return ch.entry_agent if ch else None


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
            credential_file=b_data.get("credential_file"),
            default_agent_template=b_data.get("default_agent_template"),
            lazy_create_enabled=bool(b_data.get("lazy_create_enabled", False)),
        )

    channels: dict[str, ChannelRoute] = {}
    for ch_id, ch_data in (data.get("channels") or {}).items():
        if not isinstance(ch_data, dict):
            continue
        # 归一化 channel_id: 裸名（不带 '#'）。
        # 历史 CLI 以 '#conv-xxx' 作 key 写入；V6 后所有内部逻辑统一用裸名，
        # IRC 操作端拼 '#'。这样 bridge 发 channel='conv-xxx'、CLI 写 '#conv-xxx' 都能查到。
        normalized = ch_id.lstrip("#")
        route = ChannelRoute(
            bot=ch_data.get("bot"),
            external_chat_id=ch_data.get("external_chat_id"),
            entry_agent=ch_data.get("entry_agent"),
        )
        channels[normalized] = route

    return RoutingTable(channels=channels, bots=bots)
