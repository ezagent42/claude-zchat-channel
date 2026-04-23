"""feishu_bridge.routing_reader 测试（V7: app_id 来自 credential 文件）。"""

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
        credential_file = "credentials/customer.json"
        default_agent_template = "fast-agent"
        lazy_create_enabled = true

        [bots."admin"]
        # admin 故意不写 credential_file，覆盖 read_bot_config 的 fallback 路径

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


# ---- read_bot_config (V7: app_id 来自 credential 文件) ----

def test_read_bot_config_with_credential(toml_file, tmp_path):
    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    cred_path = cred_dir / "customer.json"
    cred_path.write_text(json.dumps({"app_id": "cli_app1", "app_secret": "shh"}))

    cfg = read_bot_config(toml_file, "customer")
    assert cfg is not None
    assert cfg["name"] == "customer"
    assert cfg["app_id"] == "cli_app1"        # 来自 credential 文件
    assert cfg["app_secret"] == "shh"
    assert cfg["credential_file"] == "credentials/customer.json"
    assert cfg["default_agent_template"] == "fast-agent"
    assert cfg["lazy_create_enabled"] is True


def test_read_bot_config_missing_credential_file_field(toml_file):
    """bot 没 credential_file 字段 → app_id/app_secret 全空。"""
    cfg = read_bot_config(toml_file, "admin")
    assert cfg is not None
    assert cfg["app_id"] == ""
    assert cfg["app_secret"] is None
    assert cfg["credential_file"] is None


def test_read_bot_config_credential_file_not_on_disk(toml_file):
    """credential_file 字段存在但磁盘文件不存在 → app_id/app_secret 全空。"""
    cfg = read_bot_config(toml_file, "customer")
    # toml 里 customer 写了 credentials/customer.json，但 fixture 没建该文件
    assert cfg is not None
    assert cfg["app_id"] == ""
    assert cfg["app_secret"] is None
    assert cfg["credential_file"] == "credentials/customer.json"


def test_read_bot_config_rejects_legacy_app_id_in_toml(tmp_path):
    """V7+ : routing.toml 残留 app_id 字段 → 抛 ValueError（不静默忽略）。"""
    cred_dir = tmp_path / "credentials"
    cred_dir.mkdir()
    (cred_dir / "x.json").write_text(json.dumps({"app_id": "from_cred", "app_secret": "s"}))
    f = tmp_path / "routing.toml"
    f.write_text(textwrap.dedent("""\
        [bots."x"]
        app_id = "stale_must_be_removed"
        credential_file = "credentials/x.json"
    """))
    with pytest.raises(ValueError, match="legacy 'app_id' field"):
        read_bot_config(f, "x")


def test_read_bot_config_unknown_bot(toml_file):
    assert read_bot_config(toml_file, "ghost") is None


# ---- read_supervised_channels (V6 supervision) ----

@pytest.fixture
def supervision_toml(tmp_path: Path) -> Path:
    content = textwrap.dedent("""\
        [bots."customer"]
        credential_file = "credentials/customer.json"

        [bots."sales"]
        credential_file = "credentials/sales.json"

        [bots."squad"]
        credential_file = "credentials/squad.json"
        supervises = ["customer", "sales"]

        [bots."squad-lite"]
        credential_file = "credentials/squad-lite.json"
        supervises = ["customer"]

        [bots."squad-empty"]
        credential_file = "credentials/squad-empty.json"

        [channels."conv-a"]
        bot = "customer"
        external_chat_id = "oc_cust_a"

        [channels."conv-b"]
        bot = "customer"
        external_chat_id = "oc_cust_b"

        [channels."lead-a"]
        bot = "sales"
        external_chat_id = "oc_sales_a"

        [channels."squad-main"]
        bot = "squad"
        external_chat_id = "oc_squad_room"
    """)
    f = tmp_path / "routing.toml"
    f.write_text(content, encoding="utf-8")
    return f


def test_supervised_multiple_bots(supervision_toml):
    from feishu_bridge.routing_reader import read_supervised_channels
    m = read_supervised_channels(supervision_toml, "squad")
    assert m == {
        "oc_cust_a": "conv-a",
        "oc_cust_b": "conv-b",
        "oc_sales_a": "lead-a",
    }


def test_supervised_single_bot(supervision_toml):
    from feishu_bridge.routing_reader import read_supervised_channels
    m = read_supervised_channels(supervision_toml, "squad-lite")
    assert set(m.values()) == {"conv-a", "conv-b"}


def test_supervised_empty_for_non_squad_bot(supervision_toml):
    from feishu_bridge.routing_reader import read_supervised_channels
    assert read_supervised_channels(supervision_toml, "customer") == {}
    assert read_supervised_channels(supervision_toml, "squad-empty") == {}


def test_supervised_excludes_own_channels(supervision_toml):
    """squad bot 自己的 channel（squad-main）不出现在监管集里。"""
    from feishu_bridge.routing_reader import read_supervised_channels
    m = read_supervised_channels(supervision_toml, "squad")
    assert "squad-main" not in m.values()


def test_supervised_bot_prefix_equivalent(supervision_toml, tmp_path):
    """'bot:customer' 和 'customer' 语义等价。"""
    from feishu_bridge.routing_reader import read_supervised_channels
    content = supervision_toml.read_text().replace(
        'supervises = ["customer", "sales"]',
        'supervises = ["bot:customer", "bot:sales"]',
    )
    f2 = tmp_path / "routing2.toml"
    f2.write_text(content, encoding="utf-8")
    m = read_supervised_channels(f2, "squad")
    assert set(m.values()) == {"conv-a", "conv-b", "lead-a"}


def test_supervised_tag_prefix_v7_skipped(supervision_toml, tmp_path, caplog):
    """V7 tag: 语法不抛，只 log warning 跳过。"""
    import logging
    from feishu_bridge.routing_reader import read_supervised_channels
    content = supervision_toml.read_text().replace(
        'supervises = ["customer", "sales"]',
        'supervises = ["tag:east"]',
    )
    f2 = tmp_path / "routing3.toml"
    f2.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        m = read_supervised_channels(f2, "squad")
    assert m == {}
    assert any("not implemented" in r.message for r in caplog.records)


def test_supervised_missing_file_returns_empty(tmp_path):
    from feishu_bridge.routing_reader import read_supervised_channels
    assert read_supervised_channels(tmp_path / "nope.toml", "squad") == {}
