"""Bridge 运行时配置（V6: 全部从 routing.toml 派生）。

不再读 yaml。bridge 进程通过 `--bot <name>` 启动时，从 routing.toml 加载：
  - [bots."<name>"] → app_id / app_secret(via credential_file) / lazy_create_*
  - [channels] 里 bot=<name> 的所有 channel → external_chat 映射
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str


@dataclass
class GroupsConfig:
    """业务角色分组（squad/admin 等）— 留给 bridge 自己决定如何使用。

    当前简化：squad_chats 列出该 bridge 自身的 chat_id 集合（供镜像目标判断）。
    """
    squad_chats: list[dict] = field(default_factory=list)
    customer_chats: list[str] = field(default_factory=list)


@dataclass
class LazyCreateConfig:
    enabled: bool = False
    entry_agent_template: str = "fast-agent"
    channel_prefix: str = "conv-"


@dataclass
class BridgeConfig:
    feishu: FeishuConfig
    groups: GroupsConfig
    bot_name: str = ""                        # 在 routing.toml [bots] 里的 name
    channel_server_url: str = "ws://127.0.0.1:9999"
    upload_dir: str = ".feishu-bridge/uploads"
    customer_chats_path: str = ".feishu-bridge/customer_chats.json"
    routing_path: str = "routing.toml"
    lazy_create: LazyCreateConfig = field(default_factory=LazyCreateConfig)


def build_config_from_routing(
    routing_path: str | Path,
    bot_name: str,
    *,
    channel_server_url: str = "ws://127.0.0.1:9999",
) -> BridgeConfig:
    """从 routing.toml 构造 BridgeConfig（V6 推荐路径）。

    bot_name 必须已通过 `zchat bot add` 注册。
    """
    from feishu_bridge.routing_reader import read_bot_config, read_bridge_mappings

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

    # bridge 自身负责的 channel 的 external_chat_id 列表
    own_chats = list(read_bridge_mappings(routing_path, bot_name).keys())

    # 按 bot_name 决定 role tag —— V6 一 bot 一 bridge，role 从 bot_name 派生
    # （这是 V5 `identify_role` 依据 admin_chat_id / squad_chats / customer 三元组做分类的等价最小形）
    # 未来路径：routing.toml [bots].role 显式字段
    squad_list: list[dict] = []
    customer_list: list[str] = []
    if bot_name == "squad":
        squad_list = [{"chat_id": c} for c in own_chats]
    else:
        # customer / admin / 其他：自己的 chat 归 customer_chats（客户/管理员群）
        customer_list = own_chats

    return BridgeConfig(
        bot_name=bot_name,
        feishu=FeishuConfig(
            app_id=bot_cfg["app_id"],
            app_secret=bot_cfg["app_secret"],
        ),
        groups=GroupsConfig(
            squad_chats=squad_list,
            customer_chats=customer_list,
        ),
        channel_server_url=channel_server_url,
        upload_dir=str(bridge_subdir / "uploads"),
        customer_chats_path=str(bridge_subdir / "customer_chats.json"),
        routing_path=str(routing_path),
        lazy_create=LazyCreateConfig(
            enabled=bot_cfg.get("lazy_create_enabled", False),
            entry_agent_template=bot_cfg.get("default_agent_template") or "fast-agent",
            channel_prefix="conv-",
        ),
    )
