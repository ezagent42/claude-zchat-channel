"""plugin_loader 单元测试。

覆盖：discovery、config 注入、signature-driven DI、enabled=false、
peer 依赖、冲突检测、plugins.toml 缺失、data_dir 默认。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from channel_server.plugin import BasePlugin, PluginRegistry
from channel_server.plugin_loader import (
    default_plugin_data_dir,
    load_plugins,
    load_plugins_toml,
)


# ──────────────────────── default_plugin_data_dir ────────────────────────

def test_default_plugin_data_dir_under_routing_parent(tmp_path):
    routing = tmp_path / "routing.toml"
    p = default_plugin_data_dir("audit", routing)
    assert p == tmp_path / "plugins" / "audit"


def test_default_plugin_data_dir_works_with_string_path(tmp_path):
    p = default_plugin_data_dir("activation", str(tmp_path / "routing.toml"))
    assert p == tmp_path / "plugins" / "activation"


# ──────────────────────── load_plugins_toml ────────────────────────

def test_load_plugins_toml_missing_returns_empty(tmp_path):
    assert load_plugins_toml(tmp_path / "routing.toml") == {}


def test_load_plugins_toml_parses_nested(tmp_path):
    (tmp_path / "plugins.toml").write_text(
        '[plugins.audit]\ndata_dir = "./x"\n'
    )
    cfg = load_plugins_toml(tmp_path / "routing.toml")
    assert cfg["plugins"]["audit"]["data_dir"] == "./x"


# ──────────────────────── load_plugins · 基本发现 ────────────────────────

@pytest.fixture
def emit_event():
    return AsyncMock()


@pytest.fixture
def emit_command():
    return AsyncMock()


def test_load_builtin_plugins_all_register(tmp_path, emit_event, emit_command):
    """不给 plugins.toml，全部 builtin plugin 用默认配置加载。"""
    registry = PluginRegistry()
    registered = load_plugins(
        registry=registry,
        plugins_toml={},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    # 6 个 builtin: mode / sla / resolve / audit / activation / csat
    assert set(registered) == {"mode", "sla", "resolve", "audit", "activation", "csat"}


def test_disabled_plugin_skipped(tmp_path, emit_event, emit_command):
    registry = PluginRegistry()
    registered = load_plugins(
        registry=registry,
        plugins_toml={"plugins": {"activation": {"enabled": False}}},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    assert "activation" not in registered
    # 其它仍加载
    assert "mode" in registered
    assert "audit" in registered


def test_data_dir_from_config_propagates(tmp_path, emit_event, emit_command):
    """plugin config.data_dir 被 plugin 接收并用于 state 路径。"""
    registry = PluginRegistry()
    custom = tmp_path / "custom_audit"
    custom.mkdir()
    load_plugins(
        registry=registry,
        plugins_toml={"plugins": {"audit": {"data_dir": str(custom)}}},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    audit = registry.get_plugin("audit")
    assert audit is not None
    # Audit 内部 self._path = data_dir / "state.json"
    assert str(custom) in str(audit._path)


def test_missing_data_dir_falls_back_to_default(tmp_path, emit_event, emit_command):
    """plugins.toml 没给 data_dir → loader 注入默认 (routing_parent/plugins/<name>/)。"""
    registry = PluginRegistry()
    load_plugins(
        registry=registry,
        plugins_toml={},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    audit = registry.get_plugin("audit")
    expected = tmp_path / "plugins" / "audit"
    assert str(expected) in str(audit._path)


# ──────────────────────── Signature-driven DI ────────────────────────

def test_csat_receives_audit_via_registry_injection(tmp_path, emit_event, emit_command):
    """csat.__init__ 的 kw-only `audit` 参数被 loader 从 registry 按名注入。"""
    registry = PluginRegistry()
    load_plugins(
        registry=registry,
        plugins_toml={},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    csat = registry.get_plugin("csat")
    audit = registry.get_plugin("audit")
    assert csat is not None
    assert audit is not None
    assert csat._audit is audit


def test_csat_without_audit_still_loads(tmp_path, emit_event, emit_command):
    """audit 被禁用时 csat 仍能加载（audit=None fallback）。"""
    registry = PluginRegistry()
    registered = load_plugins(
        registry=registry,
        plugins_toml={"plugins": {"audit": {"enabled": False}}},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    assert "audit" not in registered
    assert "csat" in registered
    csat = registry.get_plugin("csat")
    assert csat._audit is None


# ──────────────────────── emit_event / emit_command 注入 ────────────────────────

def test_sla_gets_both_emit_event_and_emit_command(tmp_path, emit_event, emit_command):
    registry = PluginRegistry()
    load_plugins(
        registry=registry,
        plugins_toml={},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    sla = registry.get_plugin("sla")
    assert sla._emit_event is emit_event
    assert sla._emit_command is emit_command


def test_mode_gets_only_emit_event(tmp_path, emit_event, emit_command):
    """mode.__init__ 只声明 emit_event；不应注入无关的 emit_command。"""
    registry = PluginRegistry()
    load_plugins(
        registry=registry,
        plugins_toml={},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    mode = registry.get_plugin("mode")
    assert mode._emit_event is emit_event
    # mode 没有 _emit_command 属性（未声明）
    assert not hasattr(mode, "_emit_command")


# ──────────────────────── 业务参数透传 ────────────────────────

def test_sla_takeover_timeout_from_config(tmp_path, emit_event, emit_command):
    registry = PluginRegistry()
    load_plugins(
        registry=registry,
        plugins_toml={"plugins": {"sla": {"takeover_timeout": 60, "help_timeout": 30}}},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    sla = registry.get_plugin("sla")
    assert sla._timeout_seconds == 60.0
    assert sla._help_timeout_seconds == 30.0


# ──────────────────────── 冲突/错误处理 ────────────────────────

def test_empty_registry_allows_reregister_across_calls(tmp_path, emit_event, emit_command):
    """两次 load 到同一 registry 时 PluginRegistry 抛 duplicate ValueError，但本身不应被 loader 吞掉错误。"""
    registry = PluginRegistry()
    load_plugins(
        registry=registry,
        plugins_toml={},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    # 第二次 load 会尝试 re-register，每个 register 触发 ValueError，loader exception 安全吞掉
    registered_again = load_plugins(
        registry=registry,
        plugins_toml={},
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    # 全部跳过，没有新 register
    assert registered_again == []


# ──────────────────────── plugins.toml 扁平形态兼容 ────────────────────────

def test_flat_plugins_toml_also_accepted(tmp_path, emit_event, emit_command):
    """plugins.toml 可以是 `[audit]` 扁平或 `[plugins.audit]` 嵌套；loader 二者皆容。"""
    registry = PluginRegistry()
    load_plugins(
        registry=registry,
        plugins_toml={"audit": {"data_dir": str(tmp_path / "a")}},  # 扁平
        routing_path=tmp_path / "routing.toml",
        injections={"emit_event": emit_event, "emit_command": emit_command},
    )
    audit = registry.get_plugin("audit")
    assert audit is not None
    assert str(tmp_path / "a") in str(audit._path)
