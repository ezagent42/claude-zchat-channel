"""RoutingTable + load() 单元测试。"""

from __future__ import annotations
import textwrap
from pathlib import Path

import pytest

from channel_server.routing import ChannelRoute, RoutingTable, load


# ---- Fixtures ----

BASIC_TOML = textwrap.dedent("""\
    [channels."ch-1"]
    external_chat_id = "oc_xxx"
    bot_id = "cli_app1"
    entry_agent = "yaosh-fast-agent-001"

    [channels."ch-1".agents]
    fast-agent = "yaosh-fast-agent-001"
    deep-agent = "yaosh-deep-agent-001"

    [channels."ch-2"]

    [channels."ch-2".agents]
    helper = "alice-helper"

    [channels."ch-3"]
    external_chat_id = "oc_backup"
    bot_id = "cli_app2"
""")

MALFORMED_TOML = "this is not valid toml [\x00"


@pytest.fixture
def basic_toml_file(tmp_path: Path) -> Path:
    f = tmp_path / "routing.toml"
    f.write_text(BASIC_TOML, encoding="utf-8")
    return f


@pytest.fixture
def malformed_toml_file(tmp_path: Path) -> Path:
    f = tmp_path / "routing.toml"
    f.write_bytes(b"\xff\xfe bad toml content \x00")
    return f


# ---- Tests ----

def test_load_empty_file(tmp_path: Path):
    f = tmp_path / "routing.toml"
    f.write_text("", encoding="utf-8")
    table = load(f)
    assert isinstance(table, RoutingTable)
    assert table.channels == {}


def test_load_missing_file(tmp_path: Path):
    path = tmp_path / "nonexistent.toml"
    table = load(path)
    assert isinstance(table, RoutingTable)
    assert table.channels == {}


def test_load_basic_channels(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert set(table.channels.keys()) == {"ch-1", "ch-2", "ch-3"}
    ch1 = table.channels["ch-1"]
    assert ch1.external_chat_id == "oc_xxx"
    assert ch1.bot_id == "cli_app1"
    assert ch1.entry_agent == "yaosh-fast-agent-001"
    assert ch1.agents == {
        "fast-agent": "yaosh-fast-agent-001",
        "deep-agent": "yaosh-deep-agent-001",
    }


def test_channel_without_entry_agent(basic_toml_file: Path):
    """未设 entry_agent 时 entry_agent 属性为 None。"""
    table = load(basic_toml_file)
    ch2 = table.channels["ch-2"]
    assert ch2.entry_agent is None
    assert ch2.bot_id is None


def test_entry_agent_query(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert table.entry_agent("ch-1") == "yaosh-fast-agent-001"
    assert table.entry_agent("ch-2") is None
    assert table.entry_agent("no-such") is None


def test_channel_agents(basic_toml_file: Path):
    table = load(basic_toml_file)
    nicks = table.channel_agents("ch-1")
    assert set(nicks) == {"yaosh-fast-agent-001", "yaosh-deep-agent-001"}
    assert table.channel_agents("no-such") == []


def test_external_chat_id(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert table.external_chat_id("ch-1") == "oc_xxx"
    assert table.external_chat_id("ch-3") == "oc_backup"
    assert table.external_chat_id("ch-2") is None
    assert table.external_chat_id("no-channel") is None


def test_bot_id_field(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert table.channels["ch-1"].bot_id == "cli_app1"
    assert table.channels["ch-3"].bot_id == "cli_app2"


def test_malformed_toml_returns_empty(malformed_toml_file: Path):
    table = load(malformed_toml_file)
    assert isinstance(table, RoutingTable)
    assert table.channels == {}


def test_channel_route_defaults():
    route = ChannelRoute(channel_id="test")
    assert route.external_chat_id is None
    assert route.bot_id is None
    assert route.entry_agent is None
    assert route.agents == {}


def test_backward_compat_no_entry_agent_field(tmp_path: Path):
    """旧 routing.toml（无 entry_agent 字段）仍能加载，entry_agent=None。"""
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
    assert ch.agents == {"role": "legacy-nick"}
