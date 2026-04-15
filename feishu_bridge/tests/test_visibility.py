"""TC-018 ~ TC-019: visibility_router 单元测试。

验证 public → 双群 / side → 仅 squad 的路由逻辑。
"""

from unittest.mock import MagicMock

from feishu_bridge.visibility_router import VisibilityRouter


def test_public_goes_to_customer_and_squad():
    """TC-018: public → customer 群 + squad 群都收到。"""
    sender = MagicMock()
    gm = MagicMock()
    gm.get_customer_chat.return_value = "oc_cust"
    gm.get_squad_chat.return_value = "oc_squad"

    router = VisibilityRouter(sender=sender, group_manager=gm)
    router.route("conv_1", {"text": "hello", "visibility": "public"})

    assert sender.send_text.call_count == 2


def test_side_only_goes_to_squad():
    """TC-019: side → 只发到 squad 群，不发到 customer 群。"""
    sender = MagicMock()
    gm = MagicMock()
    gm.get_customer_chat.return_value = "oc_cust"
    gm.get_squad_chat.return_value = "oc_squad"

    router = VisibilityRouter(sender=sender, group_manager=gm)
    router.route("conv_1", {"text": "advice", "visibility": "side"})

    calls = [str(c) for c in sender.send_text.call_args_list]
    assert any("oc_squad" in c for c in calls)
    assert not any("oc_cust" in c for c in calls)
