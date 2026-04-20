"""bridge 读 routing.toml 的独立工具（V6）。

**不 import channel_server.routing**——直接用 tomllib 解析。这样 bridge 只依赖 protocol + stdlib。
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore


def _load_toml(routing_path: str | Path) -> dict:
    p = Path(routing_path)
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def read_bridge_mappings(routing_path: str | Path, bot: str) -> dict[str, str]:
    """读取 routing.toml，过滤 bot 名下的 channel，构建 external_chat_id → channel_id 映射。

    Args:
        routing_path: routing.toml 路径
        bot: 本 bridge 的 bot 名（routing.toml [bots."<name>"] 的 name）

    Returns:
        {external_chat_id: channel_id} 映射。文件不存在或解析失败返回空 dict。
    """
    data = _load_toml(routing_path)
    result: dict[str, str] = {}
    for channel_id, ch in (data.get("channels") or {}).items():
        if not isinstance(ch, dict):
            continue
        if ch.get("bot") != bot:
            continue
        ext = ch.get("external_chat_id")
        if ext:
            result[ext] = channel_id
    return result


def reverse_mapping(mappings: dict[str, str]) -> dict[str, str]:
    """翻转映射: external_chat_id → channel_id  变成  channel_id → external_chat_id."""
    return {v: k for k, v in mappings.items()}


def read_bot_config(
    routing_path: str | Path,
    bot: str,
) -> dict | None:
    """读取 routing.toml [bots."<bot>"] 完整配置 + 解析 credential_file 内容。

    Returns:
        {
          "name": str, "app_id": str, "app_secret": str | None,
          "default_agent_template": str | None,
          "lazy_create_enabled": bool,
        }
        bot 不存在返回 None。
    """
    data = _load_toml(routing_path)
    bots = data.get("bots") or {}
    if bot not in bots:
        return None
    b = bots[bot]
    out: dict = {
        "name": bot,
        "app_id": b.get("app_id", ""),
        "app_secret": None,
        "default_agent_template": b.get("default_agent_template"),
        "lazy_create_enabled": bool(b.get("lazy_create_enabled", False)),
    }
    cred = b.get("credential_file")
    if cred:
        cred_path = Path(cred)
        if not cred_path.is_absolute():
            cred_path = Path(routing_path).parent / cred
        if cred_path.exists():
            try:
                cred_data = json.loads(cred_path.read_text(encoding="utf-8"))
                out["app_secret"] = cred_data.get("app_secret")
                # 凭证文件 app_id 优先（防止 routing 与 credential 不一致）
                if cred_data.get("app_id"):
                    out["app_id"] = cred_data["app_id"]
            except Exception:
                pass
    return out
