"""OutboundRouter 按 kind 路由测试（V6 精简版）。

V6 去 role 化后：
- kind=msg/plain → send_text 到 mapper 对应的客户群（squad thread 是可选）
- kind=side → 仅在有 supervising thread 时才 thread 回复，否则 debug 忽略

注：跨 bot 的 squad 监管（让 squad 群出现客户 conv 卡片）是 V7 功能；
    V6 下 "side" 消息在常规 1-bridge-1-bot 模式下会被 drop。
"""

from unittest.mock import MagicMock

from feishu_bridge.group_manager import ChannelMapper
from feishu_bridge.outbound import ConvThread, OutboundRouter


def _setup(*, with_thread: bool = False):
    sender = MagicMock()
    mapper = ChannelMapper({"conv_1": "oc_cust"})
    router = OutboundRouter(sender=sender, mapper=mapper)
    if with_thread:
        router._threads["conv_1"] = ConvThread(
            conversation_id="conv_1",
            supervising_chat_id="oc_squad",
            card_msg_id="om_card_root",
        )
    return router, sender


def test_msg_kind_goes_to_customer_chat():
    """kind=msg → send_text 到 customer 群（V6 默认不发 squad）。"""
    router, sender = _setup()
    router.route("conv_1", kind="msg", text="hello")
    sender.send_text.assert_called_once_with("oc_cust", "hello")
    sender.reply_in_thread.assert_not_called()


def test_side_kind_with_thread_goes_to_thread_only():
    """kind=side + 有 supervising thread → thread 回复，不发 customer。"""
    router, sender = _setup(with_thread=True)
    router.route("conv_1", kind="side", text="advice")
    sender.send_text.assert_not_called()
    sender.reply_in_thread.assert_called_once()
    root, msg = sender.reply_in_thread.call_args[0]
    assert root == "om_card_root"
    assert "advice" in msg


def test_side_kind_without_thread_is_dropped():
    router, sender = _setup(with_thread=False)
    router.route("conv_1", kind="side", text="advice")
    sender.send_text.assert_not_called()
    sender.reply_in_thread.assert_not_called()
