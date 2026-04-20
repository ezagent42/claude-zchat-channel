"""feishu_bridge.routing_reader 测试（V6: bot 名 + bot_config 解析）。"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from feishu_bridge.routing_reader import (
    read_bot_config,
    read_bridge_mappings,
    reverse_mapping,
)


@pytest.fixture
def toml_file(tmp_path: Path) -> Path:
    content = textwrap.dedent("""\
        [bots."customer"]
        app_id = "cli_app1"
        credential_file = "credentials/customer.json"
        default_agent_template = "fast-agent"
        lazy_create_enabled = true

        [bots."admin"]
        app_id = "cli_app2"

        [channels."conv-a"]
        bot = "customer"
        external_chat_id = "oc_客户A"
        entry_agent = "fast-a"

        [channels."conv-b"]
        bot = "customer"
        external_chat_id = "oc_客户B"

        [channels."conv-c"]
        bot = "admin"
        external_chat_id = "oc_客户C"

        [channels."conv-d"]
        bot = "customer"
        # 没 external_chat_id → 跳过

        [channels."conv-e"]
        external_chat_id = "oc_e"
        # 没 bot → 不属于任何 bot，跳过
    """)
    f = tmp_path / "routing.toml"
    f.write_text(content, encoding="utf-8")
    return f


def test_read_filters_by_bot(toml_file):
    m = read_bridge_mappings(toml_file, bot="customer")
    assert m == {
        "oc_客户A": "conv-a",
        "oc_客户B": "conv-b",
    }


def test_read_different_bot(toml_file):
    m = read_bridge_mappings(toml_file, bot="admin")
    assert m == {"oc_客户C": "conv-c"}


def test_read_unknown_bot_empty(toml_file):
    m = read_bridge_mappings(toml_file, bot="ghost")
    assert m == {}


def test_missing_file_returns_empty(tmp_path):
    m = read_bridge_mappings(tmp_path / "nonexistent.toml", bot="x")
    assert m == {}


def test_malformed_file_returns_empty(tmp_path):
    p = tmp_path / "routing.toml"
    p.write_bytes(b"\xff\xfe bad \x00")
    m = read_bridge_mappings(p, bot="x")
    assert m == {}


def test_reverse_mapping():
    m = {"oc_A": "conv-a", "oc_B": "conv-b"}
    r = reverse_mapping(m)
    assert r == {"conv-a": "oc_A", "conv-b": "oc_B"}


def test_no_import_of_channel_server():
    """静态断言：routing_reader.py 不 import channel_server 任何东西。"""
    import ast

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


# ---- read_bot_config (V6 新增) ----

def test_read_bot_config_with_credential(toml_file, tmp_path):
    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    cred_path = cred_dir / "customer.json"
    cred_path.write_text(json.dumps({"app_id": "cli_app1", "app_secret": "shh"}))

    cfg = read_bot_config(toml_file, "customer")
    assert cfg is not None
    assert cfg["name"] == "customer"
    assert cfg["app_id"] == "cli_app1"
    assert cfg["app_secret"] == "shh"
    assert cfg["default_agent_template"] == "fast-agent"
    assert cfg["lazy_create_enabled"] is True


def test_read_bot_config_missing_credential_file(toml_file):
    cfg = read_bot_config(toml_file, "admin")
    # admin bot 在 toml 但没 credential_file
    assert cfg is not None
    assert cfg["app_id"] == "cli_app2"
    assert cfg["app_secret"] is None


def test_read_bot_config_unknown_bot(toml_file):
    assert read_bot_config(toml_file, "ghost") is None
