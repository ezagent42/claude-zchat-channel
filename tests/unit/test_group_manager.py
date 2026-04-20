"""ChannelMapper 单元测试（V6 精简版）。

V5 的 identify_role / admin_chat_id / squad_chats / 权限追踪 全部删除。
ChannelMapper 只做 channel_id ↔ feishu chat_id 双向映射（key 归一化 lstrip '#'）。
"""
from __future__ import annotations

from feishu_bridge.group_manager import ChannelMapper


def test_empty_mapper_returns_none():
    m = ChannelMapper()
    assert m.get_feishu_chat("anything") is None


def test_get_feishu_chat_basic():
    m = ChannelMapper({"conv-001": "oc_customer_a"})
    assert m.get_feishu_chat("conv-001") == "oc_customer_a"


def test_get_feishu_chat_strips_hash_prefix():
    m = ChannelMapper({"#conv-001": "oc_x"})
    assert m.get_feishu_chat("conv-001") == "oc_x"
    assert m.get_feishu_chat("#conv-001") == "oc_x"


def test_set_and_remove_mapping():
    m = ChannelMapper()
    m.set_mapping("conv-001", "oc_x")
    assert m.get_feishu_chat("conv-001") == "oc_x"
    m.remove_mapping("conv-001")
    assert m.get_feishu_chat("conv-001") is None


def test_replace_all_overwrites():
    m = ChannelMapper({"conv-old": "oc_old"})
    m.replace_all({"conv-new": "oc_new"})
    assert m.get_feishu_chat("conv-old") is None
    assert m.get_feishu_chat("conv-new") == "oc_new"


def test_backward_compat_alias():
    from feishu_bridge.group_manager import GroupManager
    assert GroupManager is ChannelMapper
