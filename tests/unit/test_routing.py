"""RoutingTable + load() 单元测试（V7）。"""

from __future__ import annotations
import textwrap
from pathlib import Path

import pytest

from channel_server.routing import Bot, ChannelRoute, RoutingTable, load


V7_TOML = textwrap.dedent("""\
    [bots."customer"]
    credential_file = "credentials/customer.json"
    default_agent_template = "fast-agent"
    lazy_create_enabled = true

    [bots."admin"]
    credential_file = "credentials/admin.json"
    lazy_create_enabled = false

    [channels."ch-1"]
    bot = "customer"
    external_chat_id = "oc_xxx"
    entry_agent = "yaosh-fast-agent-001"

    [channels."ch-2"]

    [channels."ch-3"]
    bot = "admin"
    external_chat_id = "oc_backup"
""")


@pytest.fixture
def basic_toml_file(tmp_path: Path) -> Path:
    f = tmp_path / "routing.toml"
    f.write_text(V7_TOML, encoding="utf-8")
    return f


@pytest.fixture
def malformed_toml_file(tmp_path: Path) -> Path:
    f = tmp_path / "routing.toml"
    f.write_bytes(b"\xff\xfe bad toml content \x00")
    return f


# ---- Channels ----

def test_load_empty_file(tmp_path: Path):
    f = tmp_path / "routing.toml"
    f.write_text("", encoding="utf-8")
    table = load(f)
    assert isinstance(table, RoutingTable)
    assert table.channels == {}
    assert table.bots == {}


def test_load_missing_file(tmp_path: Path):
    path = tmp_path / "nonexistent.toml"
    table = load(path)
    assert isinstance(table, RoutingTable)
    assert table.channels == {}


def test_load_basic_channels(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert set(table.channels.keys()) == {"ch-1", "ch-2", "ch-3"}
    ch1 = table.channels["ch-1"]
    assert ch1.bot == "customer"
    assert ch1.external_chat_id == "oc_xxx"
    assert ch1.entry_agent == "yaosh-fast-agent-001"


def test_channel_without_entry_agent(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert table.channels["ch-2"].entry_agent is None


def test_external_chat_id_on_route(basic_toml_file: Path):
    """ChannelRoute.external_chat_id 字段按 toml 读入。"""
    table = load(basic_toml_file)
    assert table.channels["ch-1"].external_chat_id == "oc_xxx"
    assert table.channels["ch-3"].external_chat_id == "oc_backup"
    assert table.channels["ch-2"].external_chat_id is None


def test_channel_route_defaults():
    route = ChannelRoute()
    assert route.bot is None
    assert route.external_chat_id is None
    assert route.entry_agent is None


def test_malformed_toml_returns_empty(malformed_toml_file: Path):
    table = load(malformed_toml_file)
    assert isinstance(table, RoutingTable)
    assert table.channels == {}


def test_backward_compat_no_entry_agent_field(tmp_path: Path):
    """无 entry_agent 字段仍能加载（兼容旧条目；agents 段被忽略）。"""
    legacy = textwrap.dedent("""\
        [channels."legacy"]
        external_chat_id = "oc_legacy"

        [channels."legacy".agents]
        role = "legacy-nick"
    """)
    f = tmp_path / "routing.toml"
    f.write_text(legacy, encoding="utf-8")
    table = load(f)
    ch = table.channels["legacy"]
    assert ch.entry_agent is None


# ---- Bots (V6) ----

def test_load_bots(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert set(table.bots.keys()) == {"customer", "admin"}
    customer = table.bots["customer"]
    assert customer.credential_file == "credentials/customer.json"
    assert customer.default_agent_template == "fast-agent"
    assert customer.lazy_create_enabled is True
    admin = table.bots["admin"]
    assert admin.lazy_create_enabled is False


def test_bot_dataclass_defaults():
    b = Bot(name="x")
    assert b.credential_file is None
    assert b.default_agent_template is None
    assert b.lazy_create_enabled is False


def test_load_rejects_legacy_app_id_field(tmp_path: Path):
    """V7+: 旧 routing.toml 残留 app_id 字段 → 抛 ValueError，强制用户清理（不静默忽略）。"""
    legacy = textwrap.dedent("""\
        [bots."legacy"]
        app_id = "cli_legacy_must_be_removed"
        credential_file = "credentials/legacy.json"
    """)
    f = tmp_path / "routing.toml"
    f.write_text(legacy, encoding="utf-8")
    with pytest.raises(ValueError, match="legacy 'app_id' field"):
        load(f)


def test_load_rejects_app_id_even_without_credential_file(tmp_path: Path):
    """app_id 字段单独存在（连 credential_file 都没有）也要抛错，错误信息提示默认路径。"""
    legacy = textwrap.dedent("""\
        [bots."solo"]
        app_id = "cli_solo"
    """)
    f = tmp_path / "routing.toml"
    f.write_text(legacy, encoding="utf-8")
    with pytest.raises(ValueError, match=r"credentials/solo\.json"):
        load(f)


def test_bot_instance_has_no_app_id_attr():
    """Bot dataclass 没有 app_id 字段（V7 删字段）。"""
    b = Bot(name="x")
    assert not hasattr(b, "app_id")
