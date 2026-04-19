"""YAML 配置加载 + 环境变量替换。

配置文件格式见 spec/09-feishu-bridge.md §5。
支持 ${ENV_VAR} 语法进行环境变量替换。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str


@dataclass
class GroupsConfig:
    admin_chat_id: str
    squad_chats: list[dict] = field(default_factory=list)
    customer_chats: list[str] = field(default_factory=list)


@dataclass
class LazyCreateConfig:
    """bot_added 时懒创建 channel + agent 的配置。"""
    enabled: bool = False
    entry_agent_template: str = "fast-agent"  # zchat agent create --type
    channel_prefix: str = "conv-"              # 新 channel_id 前缀


@dataclass
class BridgeConfig:
    feishu: FeishuConfig
    groups: GroupsConfig
    channel_server_url: str = "ws://127.0.0.1:9999"
    upload_dir: str = ".feishu-bridge/uploads"
    customer_chats_path: str = ".feishu-bridge/customer_chats.json"
    routing_path: str = "routing.toml"         # 从哪读 routing.toml
    lazy_create: LazyCreateConfig = field(default_factory=LazyCreateConfig)


_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _substitute_env(value: str) -> str:
    """替换 ${ENV_VAR} 为环境变量值。"""
    def _replace(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    return _ENV_PATTERN.sub(_replace, value)


def _walk_and_substitute(obj):
    """递归替换 dict/list 中的环境变量。"""
    if isinstance(obj, str):
        return _substitute_env(obj)
    if isinstance(obj, dict):
        return {k: _walk_and_substitute(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_and_substitute(item) for item in obj]
    return obj


def load_config(path: str | Path) -> BridgeConfig:
    """从 YAML 文件加载配置。"""
    raw = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    data = _walk_and_substitute(data)

    feishu = data.get("feishu", {})
    groups = data.get("groups", {})
    storage = data.get("storage", {})
    cs = data.get("channel_server", {})
    lazy = data.get("lazy_create", {})

    return BridgeConfig(
        feishu=FeishuConfig(
            app_id=feishu.get("app_id", ""),
            app_secret=feishu.get("app_secret", ""),
        ),
        groups=GroupsConfig(
            admin_chat_id=groups.get("admin_chat_id", ""),
            squad_chats=groups.get("squad_chats", []),
            customer_chats=groups.get("customer_chats", []),
        ),
        channel_server_url=cs.get("url", "ws://127.0.0.1:9999"),
        upload_dir=storage.get("upload_dir", ".feishu-bridge/uploads"),
        customer_chats_path=storage.get(
            "customer_chats_path", ".feishu-bridge/customer_chats.json"
        ),
        routing_path=storage.get("routing_path", "routing.toml"),
        lazy_create=LazyCreateConfig(
            enabled=lazy.get("enabled", False),
            entry_agent_template=lazy.get("entry_agent_template", "fast-agent"),
            channel_prefix=lazy.get("channel_prefix", "conv-"),
        ),
    )
