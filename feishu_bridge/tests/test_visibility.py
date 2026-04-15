"""TC-018 ~ TC-019: visibility_router 路由逻辑（card+thread 模型下的行为）。

原用例验证 public 双写 / side 仅 squad 的可见性规则。Task 4.6.5 将 squad
侧的发送切换为 reply_in_thread（作为 conversation card 的 thread 回复），
故断言从 send_text 调用次数改为 send_text + reply_in_thread 组合验证。
"""

from unittest.mock import MagicMock

from feishu_bridge.visibility_router import VisibilityRouter


def _setup_router_with_thread(card_msg_id: str = "om_card_root"):
    sender = MagicMock()
    gm = MagicMock()
    gm.get_customer_chat.return_value = "oc_cust"
    gm.get_squad_chat.return_value = "oc_squad"
    sender.send_card.return_value = card_msg_id
    router = VisibilityRouter(sender=sender, group_manager=gm)
    router.on_conversation_created("conv_1", metadata={})
    sender.reset_mock()
    sender.send_card.return_value = card_msg_id  # 保持以防后续调用
    return router, sender


def test_public_goes_to_customer_and_squad():
    """TC-018: public → 客户群收到 send_text，squad card 收到 reply_in_thread。"""
    router, sender = _setup_router_with_thread()
    router.route("conv_1", {"text": "hello", "visibility": "public"})

    sender.send_text.assert_called_once_with("oc_cust", "hello")
    sender.reply_in_thread.assert_called_once()
    root_arg, text_arg = sender.reply_in_thread.call_args[0]
    assert root_arg == "om_card_root"
    assert "hello" in text_arg


def test_side_only_goes_to_squad():
    """TC-019: side → 仅 squad thread 收到（客户群不发）。"""
    router, sender = _setup_router_with_thread()
    router.route("conv_1", {"text": "advice", "visibility": "side"})

    sender.send_text.assert_not_called()
    sender.reply_in_thread.assert_called_once()
    root_arg, text_arg = sender.reply_in_thread.call_args[0]
    assert root_arg == "om_card_root"
    assert "advice" in text_arg
