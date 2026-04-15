"""Task 4.6.5 单元测试：visibility_router 的 card+thread 模型。

覆盖测试计划 TC-1 ~ TC-7：
- TC-1 test_conv_created_sends_card
- TC-2 test_card_is_thread_root
- TC-3 test_public_reply_dual_write
- TC-4 test_side_thread_only
- TC-5 test_mode_changed_updates_card
- TC-6 test_conv_closed_updates_card
- TC-7 test_msg_id_mapping_for_edit
"""

from __future__ import annotations

from unittest.mock import MagicMock

from feishu_bridge.visibility_router import ConvThread, VisibilityRouter


def _build_router(
    *,
    squad_chat: str = "oc_squad",
    customer_chat: str | None = "oc_cust",
    admin_chat_id: str | None = None,
    card_msg_id: str = "om_card_root",
) -> tuple[VisibilityRouter, MagicMock]:
    sender = MagicMock()
    gm = MagicMock()
    gm.get_squad_chat.return_value = squad_chat
    gm.get_customer_chat.return_value = customer_chat
    sender.send_card.return_value = card_msg_id
    sender.send_text.return_value = "om_text_default"
    sender.reply_in_thread.return_value = "om_thread_default"
    sender.update_card.return_value = True
    sender.update_message.return_value = True
    router = VisibilityRouter(
        sender=sender, group_manager=gm, admin_chat_id=admin_chat_id
    )
    return router, sender


# ---------------------------------------------------------------------- #
# TC-1, TC-2: conversation.created → send_card → card_msg_id 存入 ConvThread
# ---------------------------------------------------------------------- #


def test_conv_created_sends_card() -> None:
    """TC-1: conversation.created → sender.send_card 被调用。"""
    router, sender = _build_router(card_msg_id="om_root_1")

    card_msg_id = router.on_conversation_created(
        "conv_1",
        metadata={"customer": {"id": "alice", "name": "Alice"}},
    )

    assert card_msg_id == "om_root_1"
    assert sender.send_card.call_count == 1
    args, _ = sender.send_card.call_args
    assert args[0] == "oc_squad"  # squad 群
    card = args[1]
    assert card["header"]["title"]["content"].startswith("对话 conv_1")


def test_card_is_thread_root() -> None:
    """TC-2: card_msg_id 存入 ConvThread（作为后续 thread 回复的 root）。"""
    router, _ = _build_router(card_msg_id="om_root_2")

    router.on_conversation_created("conv_2", metadata={})

    thread = router.get_thread("conv_2")
    assert isinstance(thread, ConvThread)
    assert thread.card_msg_id == "om_root_2"
    assert thread.squad_chat_id == "oc_squad"
    assert thread.customer_chat_id == "oc_cust"
    assert thread.state == "active"


# ---------------------------------------------------------------------- #
# TC-3: public → send_text(customer) + reply_in_thread(squad)
# ---------------------------------------------------------------------- #


def test_public_reply_dual_write() -> None:
    """TC-3: visibility=public → customer 群 send_text + squad thread reply_in_thread。"""
    router, sender = _build_router(card_msg_id="om_root_3")
    router.on_conversation_created("conv_3", metadata={})

    sender.send_text.return_value = "om_cust_msg_1"
    sender.reply_in_thread.return_value = "om_thread_msg_1"

    customer_msg_id = router.route(
        "conv_3",
        {"text": "hello", "visibility": "public", "message_id": "cs_msg_1"},
    )

    assert customer_msg_id == "om_cust_msg_1"
    sender.send_text.assert_called_once_with("oc_cust", "hello")
    sender.reply_in_thread.assert_called_once()
    root_arg, text_arg = sender.reply_in_thread.call_args[0]
    assert root_arg == "om_root_3"
    assert "→客户" in text_arg
    assert "hello" in text_arg


# ---------------------------------------------------------------------- #
# TC-4: side → 仅 reply_in_thread(squad)，不发 customer 群
# ---------------------------------------------------------------------- #


def test_side_thread_only() -> None:
    """TC-4: visibility=side → 仅 reply_in_thread，不 send_text(customer)。"""
    router, sender = _build_router(card_msg_id="om_root_4")
    router.on_conversation_created("conv_4", metadata={})

    sender.send_text.reset_mock()
    sender.reply_in_thread.reset_mock()

    router.route(
        "conv_4", {"text": "side note", "visibility": "side"}
    )

    sender.send_text.assert_not_called()
    assert sender.reply_in_thread.call_count == 1
    root_arg, text_arg = sender.reply_in_thread.call_args[0]
    assert root_arg == "om_root_4"
    assert "侧栏" in text_arg
    assert "side note" in text_arg


# ---------------------------------------------------------------------- #
# TC-5: mode.changed → update_card(card_msg_id)
# ---------------------------------------------------------------------- #


def test_mode_changed_updates_card() -> None:
    """TC-5: on_mode_changed → sender.update_card(card_msg_id, 新卡片)。"""
    router, sender = _build_router(card_msg_id="om_root_5")
    router.on_conversation_created("conv_5", metadata={})

    sender.update_card.reset_mock()
    ok = router.on_mode_changed("conv_5", mode="takeover")

    assert ok is True
    sender.update_card.assert_called_once()
    args, _ = sender.update_card.call_args
    assert args[0] == "om_root_5"
    card = args[1]
    # 新卡片应展示 takeover 模式
    first_div = next(e for e in card["elements"] if e["tag"] == "div")
    assert "人工接管" in first_div["text"]["content"]
    assert router.get_thread("conv_5").mode == "takeover"


# ---------------------------------------------------------------------- #
# TC-6: conversation.closed → update_card(state=closed)
# ---------------------------------------------------------------------- #


def test_conv_closed_updates_card() -> None:
    """TC-6: on_conversation_closed → update_card 展示"已关闭"。"""
    router, sender = _build_router(card_msg_id="om_root_6")
    router.on_conversation_created("conv_6", metadata={})

    sender.update_card.reset_mock()
    ok = router.on_conversation_closed(
        "conv_6", resolution={"outcome": "resolved", "csat_score": 5}
    )

    assert ok is True
    sender.update_card.assert_called_once()
    args, _ = sender.update_card.call_args
    assert args[0] == "om_root_6"
    card = args[1]
    assert "已关闭" in card["header"]["title"]["content"]
    # 关闭卡片不应再包含 action 按钮
    assert all(e["tag"] != "action" for e in card["elements"])
    # resolution 信息应体现在卡片内
    first_div = next(e for e in card["elements"] if e["tag"] == "div")
    assert "resolved" in first_div["text"]["content"]
    assert router.get_thread("conv_6").state == "closed"


# ---------------------------------------------------------------------- #
# TC-7: msg_id 映射 — public reply 存映射，edit 查映射
# ---------------------------------------------------------------------- #


def test_msg_id_mapping_for_edit() -> None:
    """TC-7: public reply 时存 {cs_msg_id: feishu_msg_id}，edit 查映射调 update_message。"""
    router, sender = _build_router(card_msg_id="om_root_7")
    router.on_conversation_created("conv_7", metadata={})

    sender.send_text.return_value = "om_cust_7"
    router.route(
        "conv_7",
        {"text": "original", "visibility": "public", "message_id": "cs_msg_7"},
    )
    assert router.get_feishu_msg_id("cs_msg_7") == "om_cust_7"

    sender.update_message.reset_mock()
    sender.reply_in_thread.reset_mock()
    ok = router.on_edit("conv_7", cs_msg_id="cs_msg_7", text="edited content")

    assert ok is True
    sender.update_message.assert_called_once_with("om_cust_7", "edited content")
    # edit 也在 thread 中留痕
    sender.reply_in_thread.assert_called_once()
    assert "edited" in sender.reply_in_thread.call_args[0][1].lower()


def test_edit_without_mapping_still_leaves_thread_trace() -> None:
    """边界：无 msg_id 映射时 edit 不调 update_message，但仍在 thread 追加 [edited]。"""
    router, sender = _build_router(card_msg_id="om_root_7b")
    router.on_conversation_created("conv_7b", metadata={})

    ok = router.on_edit("conv_7b", cs_msg_id="cs_unknown", text="late edit")

    assert ok is False
    sender.update_message.assert_not_called()
    sender.reply_in_thread.assert_called()
    assert "edited" in sender.reply_in_thread.call_args[0][1].lower()
