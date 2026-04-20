"""Bridge 运行时配置（V6 精简版）。

从 routing.toml `[bots."<name>"]` + 过滤 `[channels] where bot == <name>` 派生。
不再有 role / GroupsConfig — V6 一 bot 一 bridge，role 由消息级别的 __side:/__msg:
前缀决定（spec §5），不在 bridge 层分类。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str


@dataclass
class LazyCreateConfig:
    enabled: bool = False
    entry_agent_template: str = "fast-agent"
    channel_prefix: str = "conv-"


@dataclass
class BridgeConfig:
    feishu: FeishuConfig
    bot_name: str = ""
    channel_server_url: str = "ws://127.0.0.1:9999"
    upload_dir: str = ".feishu-bridge/uploads"
    routing_path: str = "routing.toml"
    lazy_create: LazyCreateConfig = field(default_factory=LazyCreateConfig)


def build_config_from_routing(
    routing_path: str | Path,
    bot_name: str,
    *,
    channel_server_url: str = "ws://127.0.0.1:9999",
) -> BridgeConfig:
    """从 routing.toml 构造 BridgeConfig。"""
    from feishu_bridge.routing_reader import read_bot_config

    bot_cfg = read_bot_config(routing_path, bot_name)
    if bot_cfg is None:
        raise ValueError(
            f"bot '{bot_name}' not found in {routing_path}; "
            f"run `zchat bot add {bot_name} --app-id ... --app-secret ...`"
        )
    if not bot_cfg.get("app_id"):
        raise ValueError(f"bot '{bot_name}' has no app_id")
    if not bot_cfg.get("app_secret"):
        raise ValueError(
            f"bot '{bot_name}' has no app_secret (credential_file missing or unreadable)"
        )

    project_dir = Path(routing_path).parent
    bridge_subdir = project_dir / f".feishu-bridge-{bot_name}"

    return BridgeConfig(
        bot_name=bot_name,
        feishu=FeishuConfig(
            app_id=bot_cfg["app_id"],
            app_secret=bot_cfg["app_secret"],
        ),
        channel_server_url=channel_server_url,
        upload_dir=str(bridge_subdir / "uploads"),
        routing_path=str(routing_path),
        lazy_create=LazyCreateConfig(
            enabled=bot_cfg.get("lazy_create_enabled", False),
            entry_agent_template=bot_cfg.get("default_agent_template") or "fast-agent",
            channel_prefix="conv-",
        ),
    )
