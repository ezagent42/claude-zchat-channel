"""feishu_bridge.routing_reader 测试。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from feishu_bridge.routing_reader import read_bridge_mappings, reverse_mapping


@pytest.fixture
def toml_file(tmp_path: Path) -> Path:
    content = textwrap.dedent("""\
        [channels."conv-a"]
        external_chat_id = "oc_客户A"
        bot_id = "cli_app1"
        entry_agent = "fast-a"

        [channels."conv-b"]
        external_chat_id = "oc_客户B"
        bot_id = "cli_app1"

        [channels."conv-c"]
        external_chat_id = "oc_客户C"
        bot_id = "cli_app2"

        [channels."conv-d"]
        # 没 external_chat_id → 跳过

        [channels."conv-e"]
        external_chat_id = "oc_e"
        # 没 bot_id → 不属于任何 bot，跳过
    """)
    f = tmp_path / "routing.toml"
    f.write_text(content, encoding="utf-8")
    return f


def test_read_filters_by_bot_id(toml_file):
    m = read_bridge_mappings(toml_file, bot_id="cli_app1")
    assert m == {
        "oc_客户A": "conv-a",
        "oc_客户B": "conv-b",
    }


def test_read_different_bot_id(toml_file):
    m = read_bridge_mappings(toml_file, bot_id="cli_app2")
    assert m == {"oc_客户C": "conv-c"}


def test_read_unknown_bot_id_empty(toml_file):
    m = read_bridge_mappings(toml_file, bot_id="cli_unknown")
    assert m == {}


def test_missing_file_returns_empty(tmp_path):
    m = read_bridge_mappings(tmp_path / "nonexistent.toml", bot_id="x")
    assert m == {}


def test_malformed_file_returns_empty(tmp_path):
    p = tmp_path / "routing.toml"
    p.write_bytes(b"\xff\xfe bad \x00")
    m = read_bridge_mappings(p, bot_id="x")
    assert m == {}


def test_reverse_mapping():
    m = {"oc_A": "conv-a", "oc_B": "conv-b"}
    r = reverse_mapping(m)
    assert r == {"conv-a": "oc_A", "conv-b": "oc_B"}


def test_no_import_of_channel_server():
    """静态断言：routing_reader.py 不 import channel_server 任何东西。"""
    import ast
    from pathlib import Path

    src_path = Path(__file__).parent.parent.parent / "src" / "feishu_bridge" / "routing_reader.py"
    tree = ast.parse(src_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module is None or "channel_server" not in node.module, \
                f"routing_reader must not import channel_server (found: {node.module})"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "channel_server" not in alias.name, \
                    f"routing_reader must not import channel_server (found: {alias.name})"
