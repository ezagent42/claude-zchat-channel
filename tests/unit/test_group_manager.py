"""TC-009 ~ TC-017: group_manager 单元测试。

覆盖 admin/squad/customer 三种角色识别、动态注册、
持久化重载、成员变动、群解散全部场景。
"""

import os
import tempfile

from feishu_bridge.group_manager import GroupManager


def test_admin_group():
    """TC-009: admin_chat_id 匹配 → 返回 admin。"""
    gm = GroupManager(admin_chat_id="oc_admin", squad_chats=[])
    assert gm.identify_role("oc_admin") == "admin"


def test_squad_group():
    """TC-010: squad_chats 匹配 → operator + operator_id。"""
    gm = GroupManager(
        admin_chat_id="oc_admin",
        squad_chats=[{"chat_id": "oc_squad_1", "operator_id": "xiaoli"}],
    )
    assert gm.identify_role("oc_squad_1") == "operator"
    assert gm.get_operator_id("oc_squad_1") == "xiaoli"


def test_unknown_group_is_unknown_before_registration():
    """TC-011: 未注册群 → unknown。"""
    gm = GroupManager(admin_chat_id="oc_admin", squad_chats=[])
    assert gm.identify_role("oc_random") == "unknown"


def test_bot_added_registers_as_customer():
    """TC-012: register_customer_chat → identify_role 返回 customer。"""
    with tempfile.TemporaryDirectory() as tmp:
        gm = GroupManager(
            admin_chat_id="oc_admin",
            squad_chats=[],
            customer_chats_path=os.path.join(tmp, "c.json"),
        )
        gm.register_customer_chat("oc_new")
        assert gm.identify_role("oc_new") == "customer"


def test_customer_chats_persisted_and_loaded():
    """TC-013: JSON 持久化 → 新实例加载后角色仍为 customer。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "c.json")
        gm = GroupManager(
            admin_chat_id="oc_admin", squad_chats=[], customer_chats_path=path
        )
        gm.register_customer_chat("oc_persist")

        gm2 = GroupManager(
            admin_chat_id="oc_admin", squad_chats=[], customer_chats_path=path
        )
        assert gm2.identify_role("oc_persist") == "customer"


def test_bot_added_to_squad_group_skipped():
    """TC-014: bot 拉入已配置 squad 群 → 不覆盖为 customer。"""
    gm = GroupManager(
        admin_chat_id="oc_admin",
        squad_chats=[{"chat_id": "oc_squad_1", "operator_id": "xiaoli"}],
    )
    gm.register_customer_chat("oc_squad_1")  # 应被忽略
    assert gm.identify_role("oc_squad_1") == "operator"


def test_member_added_to_admin_group():
    """TC-015: on_member_added(admin 群) → 获得 admin 权限。"""
    gm = GroupManager(admin_chat_id="oc_admin", squad_chats=[])
    gm.on_member_added("ou_user1", "oc_admin")
    assert gm.has_admin_permission("ou_user1")


def test_member_removed_from_squad():
    """TC-016: add → remove → 失去 operator 权限。"""
    gm = GroupManager(
        admin_chat_id="oc_admin",
        squad_chats=[{"chat_id": "oc_squad_1", "operator_id": "xiaoli"}],
    )
    gm.on_member_added("ou_op1", "oc_squad_1")
    gm.on_member_removed("ou_op1", "oc_squad_1")
    assert not gm.has_operator_permission("ou_op1", "oc_squad_1")


def test_group_disbanded_removes_customer():
    """TC-017: 群解散 → customer 变为 unknown。"""
    with tempfile.TemporaryDirectory() as tmp:
        gm = GroupManager(
            admin_chat_id="oc_admin",
            squad_chats=[],
            customer_chats_path=os.path.join(tmp, "c.json"),
        )
        gm.register_customer_chat("oc_cust1")
        gm.on_group_disbanded("oc_cust1")
        assert gm.identify_role("oc_cust1") == "unknown"


# ---------------------------------------------------------------------- #
# Task 4.6.5 扩展：auto-hijack 检测（TC-8）
# ---------------------------------------------------------------------- #


def test_operator_in_customer_chat():
    """TC-8: 已知 operator 在动态注册的 customer 群里发言 → 返回 True。"""
    with tempfile.TemporaryDirectory() as tmp:
        gm = GroupManager(
            admin_chat_id="oc_admin",
            squad_chats=[{"chat_id": "oc_squad_1", "operator_id": "xiaoli"}],
            customer_chats_path=os.path.join(tmp, "c.json"),
        )
        gm.register_customer_chat("oc_cust_42")
        gm.on_member_added("ou_op1", "oc_squad_1")

        # ou_op1 是已知 operator，oc_cust_42 是 customer 群 → auto-hijack 条件成立
        assert gm.is_operator_in_customer_chat("ou_op1", "oc_cust_42") is True

        # 在 squad 群内发言：非 customer 群，不触发 auto-hijack
        assert gm.is_operator_in_customer_chat("ou_op1", "oc_squad_1") is False

        # 非 operator 用户在 customer 群发言：不触发
        assert gm.is_operator_in_customer_chat("ou_guest", "oc_cust_42") is False

        # 未注册群：不触发
        assert gm.is_operator_in_customer_chat("ou_op1", "oc_unknown") is False


# ---------------------------------------------------------------------- #
# V4: channel_chat_map 映射测试
# ---------------------------------------------------------------------- #


def test_get_customer_chat_from_channel_map():
    """V4: channel_id → external_chat_id 映射查询。"""
    gm = GroupManager(
        admin_chat_id="oc_admin",
        squad_chats=[],
        channel_chat_map={"ch-群A": "oc_客户群A", "ch-群B": "oc_客户群B"},
    )
    assert gm.get_customer_chat("ch-群A") == "oc_客户群A"
    assert gm.get_customer_chat("ch-群B") == "oc_客户群B"
    assert gm.get_customer_chat("ch-unknown") is None


def test_get_customer_chat_fallback_to_dynamic():
    """V4: channel_chat_map 未命中时降级到动态注册的 chat_id。"""
    with tempfile.TemporaryDirectory() as tmp:
        gm = GroupManager(
            admin_chat_id="oc_admin",
            squad_chats=[],
            customer_chats_path=os.path.join(tmp, "c.json"),
            channel_chat_map={"ch-群A": "oc_客户群A"},
        )
        gm.register_customer_chat("oc_direct")
        # 映射命中
        assert gm.get_customer_chat("ch-群A") == "oc_客户群A"
        # 降级到动态注册
        assert gm.get_customer_chat("oc_direct") == "oc_direct"
        # 都没有
        assert gm.get_customer_chat("unknown") is None


def test_set_and_remove_channel_mapping():
    """V4: 动态添加/删除映射。"""
    gm = GroupManager(admin_chat_id="oc_admin", squad_chats=[])
    assert gm.get_customer_chat("ch-new") is None

    gm.set_channel_mapping("ch-new", "oc_new_chat")
    assert gm.get_customer_chat("ch-new") == "oc_new_chat"

    gm.remove_channel_mapping("ch-new")
    assert gm.get_customer_chat("ch-new") is None
