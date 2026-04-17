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

    [channels."ch-1".agents]
    fast-agent = "yaosh-fast-agent-001"
    deep-agent = "yaosh-deep-agent-001"

    [channels."ch-2"]

    [channels."ch-2".agents]
    helper = "alice-helper"
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
    assert "ch-1" in table.channels
    assert "ch-2" in table.channels
    ch1 = table.channels["ch-1"]
    assert ch1.external_chat_id == "oc_xxx"
    assert ch1.agents == {
        "fast-agent": "yaosh-fast-agent-001",
        "deep-agent": "yaosh-deep-agent-001",
    }


def test_channel_agents(basic_toml_file: Path):
    table = load(basic_toml_file)
    nicks = table.channel_agents("ch-1")
    assert set(nicks) == {"yaosh-fast-agent-001", "yaosh-deep-agent-001"}
    assert table.channel_agents("no-such") == []


def test_external_chat_id(basic_toml_file: Path):
    table = load(basic_toml_file)
    assert table.external_chat_id("ch-1") == "oc_xxx"
    assert table.external_chat_id("ch-2") is None
    assert table.external_chat_id("no-channel") is None


def test_malformed_toml_returns_empty(malformed_toml_file: Path):
    table = load(malformed_toml_file)
    assert isinstance(table, RoutingTable)
    assert table.channels == {}


def test_channel_route_defaults():
    route = ChannelRoute(channel_id="test")
    assert route.external_chat_id is None
    assert route.agents == {}
