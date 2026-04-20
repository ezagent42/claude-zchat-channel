"""OutboundRouter 单元测试（V6 精简版）。

V6 去 role 化：OutboundRouter 只依赖 ChannelMapper（channel_id → feishu chat_id）。
- msg/plain: send_text 到 mapper 对应的飞书群
- side: 若有 supervising thread 则 thread 回复（V6 默认无跨 bot 监管）
- edit: 查 _msg_id_map 做 update_message
- on_conversation_created: V6 不再自动发监管卡片（squad bridge 未来扩展处理）
"""
from __future__ import annotations

from unittest.mock import MagicMock

from feishu_bridge.group_manager import ChannelMapper
from feishu_bridge.outbound import ConvThread, OutboundRouter


def _build_router(
    *,
    mapping: dict[str, str] | None = None,
) -> tuple[OutboundRouter, MagicMock]:
    sender = MagicMock()
    sender.send_text.return_value = "om_text_default"
    sender.send_card.return_value = "om_card_default"
    sender.reply_in_thread.return_value = "om_thread_default"
    sender.update_card.return_value = True
    sender.update_message.return_value = True
    mapper = ChannelMapper(mapping or {"conv_1": "oc_cust_a"})
    return OutboundRouter(sender=sender, mapper=mapper), sender


# ---------- conversation.created（V6: no-op by default） ----------

def test_conv_created_no_op_v6() -> None:
    router, sender = _build_router()
    result = router.on_conversation_created("conv_1", metadata={"source": "feishu"})
    assert result is None
    sender.send_card.assert_not_called()


# ---------- msg/plain routing ----------

def test_msg_kind_sends_to_customer_chat() -> None:
    router, sender = _build_router(mapping={"conv_1": "oc_customer"})
    router.route("conv_1", kind="msg", text="hello customer")
    sender.send_text.assert_called_once_with("oc_customer", "hello customer")


def test_plain_kind_sends_to_customer_chat() -> None:
    router, sender = _build_router(mapping={"conv_1": "oc_customer"})
    router.route("conv_1", kind="plain", text="hi plain")
    sender.send_text.assert_called_once_with("oc_customer", "hi plain")


def test_msg_with_no_mapping_is_dropped() -> None:
    router, sender = _build_router(mapping={})
    router.route("ghost", kind="msg", text="never delivered")
    sender.send_text.assert_not_called()


def test_msg_fallback_to_conversation_id_if_oc_prefix() -> None:
    """V5 容错：conv_id = oc_xxx 直接当 chat_id。"""
    router, sender = _build_router(mapping={})
    router.route("oc_direct", kind="msg", text="legacy")
    sender.send_text.assert_called_once_with("oc_direct", "legacy")


# ---------- side routing（需要 supervising thread） ----------

def test_side_kind_with_thread_goes_to_thread() -> None:
    router, sender = _build_router()
    router._threads["conv_1"] = ConvThread(
        conversation_id="conv_1",
        supervising_chat_id="oc_squad",
        card_msg_id="om_card_root",
    )
    router.route("conv_1", kind="side", text="operator suggestion")
    sender.reply_in_thread.assert_called_once()
    args, _ = sender.reply_in_thread.call_args
    assert args[0] == "om_card_root"
    assert "operator suggestion" in args[1]


def test_side_kind_without_thread_is_dropped() -> None:
    """V6 默认无跨 bot 监管，side 消息可能没有 thread。"""
    router, sender = _build_router()
    router.route("conv_1", kind="side", text="nowhere to go")
    sender.reply_in_thread.assert_not_called()


# ---------- edit routing ----------

def test_edit_with_msg_id_calls_update_message() -> None:
    router, sender = _build_router()
    router._msg_id_map["cs_msg_1"] = "om_feishu_1"
    result = router.on_edit("conv_1", "cs_msg_1", "edited text")
    sender.update_message.assert_called_once_with("om_feishu_1", "edited text")
    assert result is True


def test_edit_without_mapping_noop() -> None:
    router, sender = _build_router()
    result = router.on_edit("conv_1", "unknown_cs_msg", "edit")
    sender.update_message.assert_not_called()
    assert result is False


def test_msg_with_cs_msg_id_populates_map_for_future_edit() -> None:
    router, sender = _build_router(mapping={"conv_1": "oc_customer"})
    sender.send_text.return_value = "om_feishu_sent"
    router.route("conv_1", kind="msg", text="hi", cs_msg_id="cs_1")
    assert router.get_feishu_msg_id("cs_1") == "om_feishu_sent"


# ---------- csat ----------

def test_csat_request_sends_card_to_customer() -> None:
    router, sender = _build_router(mapping={"conv_1": "oc_customer"})
    router.on_csat_request("conv_1")
    sender.send_card.assert_called_once()
    args, _ = sender.send_card.call_args
    assert args[0] == "oc_customer"


# ---------- thread/conversation state ----------

def test_get_conversation_for_thread() -> None:
    router, _ = _build_router()
    router._threads["conv_1"] = ConvThread(
        conversation_id="conv_1",
        supervising_chat_id="oc_squad_a",
        state="active",
    )
    assert router.get_conversation_for_thread("oc_squad_a") == "conv_1"
    assert router.get_conversation_for_thread("oc_unknown") is None


def test_mode_changed_requires_card() -> None:
    router, sender = _build_router()
    # 没有 thread → 返回 False
    assert router.on_mode_changed("ghost", "takeover") is False
    # 有 thread 有 card → update_card 被调
    router._threads["conv_1"] = ConvThread(
        conversation_id="conv_1",
        supervising_chat_id="oc_sq",
        card_msg_id="om_root",
    )
    assert router.on_mode_changed("conv_1", "takeover") is True
    sender.update_card.assert_called_once()


def test_conv_closed_requires_card() -> None:
    router, sender = _build_router()
    assert router.on_conversation_closed("ghost") is False
    router._threads["conv_1"] = ConvThread(
        conversation_id="conv_1",
        supervising_chat_id="oc_sq",
        card_msg_id="om_root",
    )
    assert router.on_conversation_closed("conv_1") is True
    thread = router.get_thread("conv_1")
    assert thread.state == "closed"
