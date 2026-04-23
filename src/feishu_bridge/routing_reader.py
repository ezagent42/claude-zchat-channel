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


def read_supervised_channels(
    routing_path: str | Path,
    squad_bot: str,
) -> dict[str, str]:
    """读取 squad_bot 监管的所有 channels，返回 {external_chat_id: channel_id}。

    解析 `[bots."<squad_bot>"].supervises` 列表，按前缀语法匹配：
      - 无前缀 / "bot:<name>" — 匹配 channels 里 bot == <name>
      - "tag:<label>"         — V7+，当前记 warning 跳过
      - "pattern:<glob>"      — V7+，当前记 warning 跳过

    squad_bot 自己拥有的 channels（bot == squad_bot）排除（那是自己的，不是监管）。
    """
    data = _load_toml(routing_path)
    bots = data.get("bots") or {}
    bot_cfg = bots.get(squad_bot) or {}
    supervises = bot_cfg.get("supervises") or []

    # 解析 supervises 列表为匹配规则
    matchers: list[tuple[str, str]] = []  # (kind, value) e.g. ("bot", "customer")
    for entry in supervises:
        if not isinstance(entry, str):
            continue
        if ":" not in entry:
            matchers.append(("bot", entry))
        else:
            prefix, _, value = entry.partition(":")
            if prefix not in ("bot",):
                # V7+ 保留语法（tag / pattern），V6 不实现
                import logging as _logging
                _logging.getLogger("feishu-bridge.routing_reader").warning(
                    "supervises prefix %r not implemented in V6; skipping entry %r",
                    prefix, entry,
                )
                continue
            matchers.append((prefix, value))

    if not matchers:
        return {}

    result: dict[str, str] = {}
    for ch_id, ch in (data.get("channels") or {}).items():
        if not isinstance(ch, dict):
            continue
        ch_bot = ch.get("bot")
        if ch_bot == squad_bot:
            # 自己的 channel 不算监管
            continue
        ext = ch.get("external_chat_id")
        if not ext:
            continue
        for kind, value in matchers:
            if kind == "bot" and ch_bot == value:
                result[ext] = ch_id
                break
    return result


def read_bot_config(
    routing_path: str | Path,
    bot: str,
) -> dict | None:
    """读取 routing.toml [bots."<bot>"] 配置 + 从 credential_file 读 app_id/app_secret。

    V7：credential_file 是 app_id 的唯一来源。routing.toml 不再写 app_id。
    旧 routing.toml 残留 'app_id' 字段会抛 ValueError（强制升级清理）。

    Returns:
        {
          "name": str, "app_id": str, "app_secret": str | None,
          "default_agent_template": str | None,
          "lazy_create_enabled": bool,
          "credential_file": str | None,   # 原始相对路径（debug 用）
        }
        bot 不存在返回 None。
        credential_file 缺失/不可读 → app_id 与 app_secret 均为 ""/None（调用方检查）。
    """
    data = _load_toml(routing_path)
    bots = data.get("bots") or {}
    if bot not in bots:
        return None
    b = bots[bot]
    if "app_id" in b:
        raise ValueError(
            f"routing.toml [bots.\"{bot}\"] contains legacy 'app_id' field — "
            f"V7+ moved app_id into credential_file "
            f"({b.get('credential_file', f'credentials/{bot}.json')}). "
            f"Delete the 'app_id = ...' line from routing.toml and re-run."
        )
    cred = b.get("credential_file")
    out: dict = {
        "name": bot,
        "app_id": "",
        "app_secret": None,
        "default_agent_template": b.get("default_agent_template"),
        "lazy_create_enabled": bool(b.get("lazy_create_enabled", False)),
        "credential_file": cred,
    }
    if cred:
        cred_path = Path(cred)
        if not cred_path.is_absolute():
            cred_path = Path(routing_path).parent / cred
        if cred_path.exists():
            try:
                cred_data = json.loads(cred_path.read_text(encoding="utf-8"))
                out["app_id"] = cred_data.get("app_id", "")
                out["app_secret"] = cred_data.get("app_secret")
            except Exception:
                pass
    return out
