"""Commands 原语 — 命令解析 (spec §8)"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Command:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


# 命令定义：name → 参数名列表（按位置顺序）
_COMMAND_DEFS: dict[str, list[str]] = {
    "hijack": [],
    "release": [],
    "copilot": [],
    "resolve": [],
    "abandon": [],
    "assign": ["agent_nick", "operator_id"],
    "reassign": ["agent_nick", "from_operator", "to_operator"],
    "squad": ["target"],
    "status": ["target"],
    "dispatch": ["conversation_id", "agent_nick"],
    "review": [],
}


def parse_command(text: str) -> Command | None:
    """解析 / 开头的命令。非命令返回 None，未知命令返回 name='unknown'。"""
    if not text.startswith("/"):
        return None

    parts = text.split()
    name = parts[0][1:]  # 去掉 /
    positional = parts[1:]

    if name not in _COMMAND_DEFS:
        return Command(name="unknown", raw=text)

    param_names = _COMMAND_DEFS[name]
    args: dict[str, Any] = {}
    for i, param_name in enumerate(param_names):
        if i < len(positional):
            args[param_name] = positional[i]
        else:
            args[param_name] = ""  # 缺失参数填空字符串，handler 侧校验

    return Command(name=name, args=args, raw=text)
