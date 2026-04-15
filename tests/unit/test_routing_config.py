"""Unit 测试 routing_config.py — routing.toml 解析 + 白名单验证。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from routing_config import RoutingConfig, load_routing_config


# ------------------------------------------------------------------ #
# load_routing_config
# ------------------------------------------------------------------ #


def test_load_routing_config_full() -> None:
    """正确解析 routing.toml 中的 [routing] 段。"""
    content = b"""
[routing]
default_agents = ["fast-agent"]
escalation_chain = ["deep-agent", "operator"]
available_agents = ["fast-agent", "deep-agent", "translation-agent"]
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(content)
        f.flush()
        cfg = load_routing_config(f.name)

    assert cfg.default_agents == ["fast-agent"]
    assert cfg.escalation_chain == ["deep-agent", "operator"]
    assert cfg.available_agents == ["fast-agent", "deep-agent", "translation-agent"]


def test_load_missing_config() -> None:
    """文件不存在时返回空默认配置（不报错）。"""
    cfg = load_routing_config("/nonexistent/routing.toml")
    assert cfg.default_agents == []
    assert cfg.escalation_chain == []
    assert cfg.available_agents == []


def test_load_empty_config() -> None:
    """空 TOML 文件 → 空默认配置。"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(b"")
        f.flush()
        cfg = load_routing_config(f.name)

    assert cfg.default_agents == []


def test_load_partial_config() -> None:
    """只有部分字段 → 缺失字段用默认值。"""
    content = b"""
[routing]
default_agents = ["agent0"]
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(content)
        f.flush()
        cfg = load_routing_config(f.name)

    assert cfg.default_agents == ["agent0"]
    assert cfg.escalation_chain == []
    assert cfg.available_agents == []


def test_load_malformed_config() -> None:
    """格式错误的 TOML → 返回默认配置（不崩溃）。"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(b"this is not valid toml {{{{")
        f.flush()
        cfg = load_routing_config(f.name)

    assert cfg.default_agents == []


# ------------------------------------------------------------------ #
# RoutingConfig.is_dispatch_allowed
# ------------------------------------------------------------------ #


def test_dispatch_whitelist_pass() -> None:
    """agent 在白名单中 → 允许。"""
    cfg = RoutingConfig(available_agents=["fast-agent", "deep-agent"])
    assert cfg.is_dispatch_allowed("fast-agent") is True


def test_dispatch_whitelist_reject() -> None:
    """agent 不在白名单中 → 拒绝。"""
    cfg = RoutingConfig(available_agents=["fast-agent", "deep-agent"])
    assert cfg.is_dispatch_allowed("rogue-agent") is False


def test_dispatch_empty_whitelist() -> None:
    """白名单为空 → 不限制，任何 agent 都允许。"""
    cfg = RoutingConfig(available_agents=[])
    assert cfg.is_dispatch_allowed("any-agent") is True


def test_dispatch_default_config() -> None:
    """默认配置（无白名单） → 不限制。"""
    cfg = RoutingConfig()
    assert cfg.is_dispatch_allowed("whatever") is True
