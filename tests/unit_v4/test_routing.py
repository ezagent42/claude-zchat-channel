"""RoutingTable + load() 单元测试。"""

from __future__ import annotations
import textwrap
from pathlib import Path

import pytest

from channel_server.routing import ChannelRoute, RoutingTable, load


# ---- Fixtures ----

BASIC_TOML = textwrap.dedent("""\
    [channels."ch-1"]
    feishu_chat_id = "oc_xxx"
    squad_chat_id = "oc_squad"
    squad_thread_root = "om_xxx"
    default_agents = ["fast-agent"]

    [channels."ch-1".agents]
    fast-agent = "yaosh-fast-agent-001"
    deep-agent = "yaosh-deep-agent-001"

    [channels."ch-2"]
    default_agents = []

    [channels."ch-2".agents]
    helper = "alice-helper"

    [operators]
    "ou_xxx" = {name = "alice", capability = "squad"}
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
    assert table.operators == {}


def test_load_missing_file(tmp_path: Path):
    path = tmp_path / "nonexistent.toml"
    table = load(path)
    assert isinstance(table, RoutingTable)
    assert table.channels == {}


def test_load_basic_channels(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert "ch-1" in table.channels
    assert "ch-2" in table.channels
    ch1 = table.channels["ch-1"]
    assert ch1.feishu_chat_id == "oc_xxx"
    assert ch1.squad_chat_id == "oc_squad"
    assert ch1.squad_thread_root == "om_xxx"
    assert ch1.default_agents == ["fast-agent"]
    assert ch1.agents == {
        "fast-agent": "yaosh-fast-agent-001",
        "deep-agent": "yaosh-deep-agent-001",
    }
    assert "ou_xxx" in table.operators
    assert table.operators["ou_xxx"]["name"] == "alice"


def test_resolve_agent(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert table.resolve_agent("ch-1", "fast-agent") == "yaosh-fast-agent-001"
    assert table.resolve_agent("ch-1", "deep-agent") == "yaosh-deep-agent-001"
    assert table.resolve_agent("ch-1", "nonexistent") is None
    assert table.resolve_agent("no-such-channel", "fast-agent") is None


def test_channel_agents(basic_toml_file: Path):
    table = load(basic_toml_file)
    nicks = table.channel_agents("ch-1")
    assert set(nicks) == {"yaosh-fast-agent-001", "yaosh-deep-agent-001"}
    assert table.channel_agents("no-such") == []


def test_identify_nick(basic_toml_file: Path):
    table = load(basic_toml_file)
    role, ch_id = table.identify_nick("yaosh-fast-agent-001")
    assert role == "fast-agent"
    assert ch_id == "ch-1"

    role2, ch_id2 = table.identify_nick("alice-helper")
    assert role2 == "helper"
    assert ch_id2 == "ch-2"

    role3, ch_id3 = table.identify_nick("unknown-nick")
    assert role3 is None
    assert ch_id3 is None


def test_feishu_mapping(basic_toml_file: Path):
    table = load(basic_toml_file)
    customer, squad, thread = table.feishu_mapping("ch-1")
    assert customer == "oc_xxx"
    assert squad == "oc_squad"
    assert thread == "om_xxx"

    # ch-2 没有 feishu 字段
    c2, s2, t2 = table.feishu_mapping("ch-2")
    assert c2 is None
    assert s2 is None
    assert t2 is None

    # 不存在的 channel
    c3, s3, t3 = table.feishu_mapping("no-channel")
    assert c3 is None
    assert s3 is None
    assert t3 is None


def test_malformed_toml_returns_empty(malformed_toml_file: Path):
    table = load(malformed_toml_file)
    assert isinstance(table, RoutingTable)
    assert table.channels == {}


def test_channel_route_defaults():
    route = ChannelRoute(channel_id="test")
    assert route.feishu_chat_id is None
    assert route.squad_chat_id is None
    assert route.squad_thread_root is None
    assert route.default_agents == []
    assert route.agents == {}
