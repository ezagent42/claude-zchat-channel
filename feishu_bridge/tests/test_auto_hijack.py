"""Task 4.6.5 TC-9 单元测试：bridge._on_message 自动检测 auto-hijack。

完整 E2E（test_card_thread_e2e）需要真实飞书凭证，故本测试以 mock 事件
验证 bridge.py 的检测逻辑：已知 operator 在 customer 群发言时，会触发
on_auto_hijack 回调（后续 app 层将该回调接到 Bridge API）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from feishu_bridge.bridge import FeishuBridge
from feishu_bridge.config import BridgeConfig, FeishuConfig, GroupsConfig


def _build_bridge(tmp_path) -> FeishuBridge:
    cfg = BridgeConfig(
        feishu=FeishuConfig(app_id="cli_test", app_secret="secret"),
        groups=GroupsConfig(
            admin_chat_id="oc_admin",
            squad_chats=[{"chat_id": "oc_squad_1", "operator_id": "xiaoli"}],
        ),
        upload_dir=str(tmp_path / "uploads"),
        customer_chats_path=str(tmp_path / "customer_chats.json"),
    )
    return FeishuBridge(cfg)


def _mock_message_event(chat_id: str, sender_open_id: str, text: str = "hi") -> SimpleNamespace:
    """构造符合 P2ImMessageReceiveV1 结构的假事件。"""
    import json

    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_id=chat_id,
                message_type="text",
                content=json.dumps({"text": text}),
                message_id="om_msg_1",
                create_time="0",
            ),
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(open_id=sender_open_id),
            ),
        )
    )


def test_operator_in_customer_chat_triggers_hijack_callback(tmp_path) -> None:
    """TC-9: 已知 operator 在 customer 群发言 → on_auto_hijack 被调用。"""
    bridge = _build_bridge(tmp_path)
    bridge.group_manager.register_customer_chat("oc_cust_77")
    bridge.group_manager.on_member_added("ou_op1", "oc_squad_1")

    callback = MagicMock()
    bridge.on_auto_hijack = callback

    data = _mock_message_event("oc_cust_77", "ou_op1", text="I'll take over")
    bridge._on_message(data)

    assert callback.call_count == 1
    args, _ = callback.call_args
    assert args[0] == "oc_cust_77"  # conversation_id 用 chat_id 标识
    assert args[1] == "ou_op1"  # operator open_id
    assert args[2] == "I'll take over"


def test_customer_in_customer_chat_does_not_trigger(tmp_path) -> None:
    """普通 customer 在 customer 群发言 → 不触发 auto-hijack。"""
    bridge = _build_bridge(tmp_path)
    bridge.group_manager.register_customer_chat("oc_cust_77")

    callback = MagicMock()
    bridge.on_auto_hijack = callback

    data = _mock_message_event("oc_cust_77", "ou_customer", text="need help")
    bridge._on_message(data)

    callback.assert_not_called()


def test_operator_in_squad_chat_does_not_trigger(tmp_path) -> None:
    """operator 在 squad 群内发言 → 不触发 auto-hijack（那是正常侧栏）。"""
    bridge = _build_bridge(tmp_path)
    bridge.group_manager.on_member_added("ou_op1", "oc_squad_1")

    callback = MagicMock()
    bridge.on_auto_hijack = callback

    data = _mock_message_event("oc_squad_1", "ou_op1", text="note")
    bridge._on_message(data)

    callback.assert_not_called()


def test_auto_hijack_callback_exception_is_swallowed(tmp_path) -> None:
    """回调抛异常时不应中断主消息处理流程。"""
    bridge = _build_bridge(tmp_path)
    bridge.group_manager.register_customer_chat("oc_cust_77")
    bridge.group_manager.on_member_added("ou_op1", "oc_squad_1")

    def boom(*_args, **_kw):
        raise RuntimeError("callback broken")

    bridge.on_auto_hijack = boom

    data = _mock_message_event("oc_cust_77", "ou_op1")
    # 不应抛异常
    bridge._on_message(data)
