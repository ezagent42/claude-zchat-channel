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
