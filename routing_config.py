"""Routing 配置加载 — routing.toml 解析 + 白名单验证 (spec §6 Agent 编排)。

channel-server 启动时加载 routing.toml，提供：
- default_agents: 新 conversation 自动 dispatch 的 agent 列表
- escalation_chain: 升级时按顺序尝试 dispatch 的列表
- available_agents: /dispatch 命令白名单（空 = 不限制）
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class RoutingConfig:
    """routing.toml 的 [routing] 段，代表全局路由策略（immutable）。

    所有字段默认为空列表，对应"不限制 / 不自动触发"语义，
    详见 routing.example.toml 的注释和 README Routing Configuration 章节。
    """

    default_agents: list[str] = field(default_factory=list)
    """新 conversation 到达时自动 dispatch 的 agent IRC nick 列表。

    空列表 → 不自动 dispatch，需 operator 手动使用 /dispatch 命令。
    """

    escalation_chain: list[str] = field(default_factory=list)
    """升级链 — SLA 超时或主动升级时按列表顺序依次尝试 dispatch。

    空列表 → 不自动升级，超时仅写事件日志。
    特殊值 "operator" 表示升级到人工运营席位。
    """

    available_agents: list[str] = field(default_factory=list)
    """/dispatch 命令白名单（按 agent IRC nick）。

    空列表 → 不限制，operator 可 dispatch 到任意 agent。
    非空时只允许 dispatch 到列表中的 agent，其余返回权限拒绝。
    """

    def is_dispatch_allowed(self, agent_nick: str) -> bool:
        """白名单为空 → 不限制；非空 → agent_nick 必须在列表中。"""
        if not self.available_agents:
            return True
        return agent_nick in self.available_agents


def load_routing_config(path: str | Path) -> RoutingConfig:
    """从 routing.toml 加载配置。文件不存在时返回空默认值（不报错）。"""
    p = Path(path)
    if not p.exists():
        logger.info("routing config not found: %s — using defaults", p)
        return RoutingConfig()

    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        logger.warning("failed to parse routing config %s: %s — using defaults", p, e)
        return RoutingConfig()

    routing: dict[str, Any] = data.get("routing", {})
    return RoutingConfig(
        default_agents=list(routing.get("default_agents", [])),
        escalation_chain=list(routing.get("escalation_chain", [])),
        available_agents=list(routing.get("available_agents", [])),
    )
