"""bridge 读 routing.toml 的独立工具。

**不 import channel_server.routing**——直接用 tomllib 解析。这样 bridge 只依赖 protocol + stdlib。
"""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore


def read_bridge_mappings(routing_path: str | Path, bot_id: str) -> dict[str, str]:
    """读取 routing.toml，过滤本 bot_id 的 channel，构建 external_chat_id → channel_id 映射。

    Args:
        routing_path: routing.toml 路径
        bot_id: 本 bridge 的 bot_id（飞书 app_id），用于过滤

    Returns:
        {external_chat_id: channel_id} 映射。文件不存在或解析失败返回空 dict。
    """
    p = Path(routing_path)
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return {}

    result: dict[str, str] = {}
    for channel_id, ch in (data.get("channels") or {}).items():
        if not isinstance(ch, dict):
            continue
        if ch.get("bot_id") != bot_id:
            continue
        ext = ch.get("external_chat_id")
        if ext:
            result[ext] = channel_id
    return result


def reverse_mapping(mappings: dict[str, str]) -> dict[str, str]:
    """翻转映射: external_chat_id → channel_id  变成  channel_id → external_chat_id."""
    return {v: k for k, v in mappings.items()}
