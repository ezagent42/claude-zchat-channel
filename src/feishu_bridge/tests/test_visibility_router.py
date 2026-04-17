"""OutboundRouter V4 路由逻辑测试（替换旧 visibility_router 测试）。

V4 协议：按 kind 参数路由，不读 visibility 字段。
TC-018: kind=msg/plain → 客户群 send_text + squad thread reply_in_thread
TC-019: kind=side → 仅 squad thread（客户群不发）
"""

from unittest.mock import MagicMock

from feishu_bridge.outbound import OutboundRouter


def _setup_router_with_thread(card_msg_id: str = "om_card_root"):
    sender = MagicMock()
    gm = MagicMock()
    gm.get_customer_chat.return_value = "oc_cust"
    gm.get_squad_chat.return_value = "oc_squad"
    sender.send_card.return_value = card_msg_id
    router = OutboundRouter(sender=sender, group_manager=gm)
    router.on_conversation_created("conv_1", metadata={})
    sender.reset_mock()
    sender.send_card.return_value = card_msg_id  # 保持以防后续调用
    return router, sender


def test_msg_kind_goes_to_customer_and_squad():
    """TC-018: kind=msg → 客户群收到 send_text，squad card 收到 reply_in_thread。"""
    router, sender = _setup_router_with_thread()
    router.route("conv_1", kind="msg", text="hello")

    sender.send_text.assert_called_once_with("oc_cust", "hello")
    sender.reply_in_thread.assert_called_once()
    root_arg, text_arg = sender.reply_in_thread.call_args[0]
    assert root_arg == "om_card_root"
    assert "hello" in text_arg


def test_side_kind_only_goes_to_squad():
    """TC-019: kind=side → 仅 squad thread 收到（客户群不发）。"""
    router, sender = _setup_router_with_thread()
    router.route("conv_1", kind="side", text="advice")

    sender.send_text.assert_not_called()
    sender.reply_in_thread.assert_called_once()
    root_arg, text_arg = sender.reply_in_thread.call_args[0]
    assert root_arg == "om_card_root"
    assert "advice" in text_arg
