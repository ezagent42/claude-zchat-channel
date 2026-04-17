"""Unit tests: FeishuTestClient 7 个扩展方法。

覆盖 Phase Final eval-doc BEH-1 ~ BEH-7:
- assert_message_edited — 轮询检测 content 变化
- assert_card_appears — 过滤 msg_type=interactive
- assert_card_updated — card content 变化检测
- send_thread_reply — reply_in_thread=True API 调用
- assert_thread_message_appears — root_id 过滤
- send_message_as_operator — 委托 send_message
- click_card_action — 构造 payload 存储
"""

from __future__ import annotations

import json
from functools import partial

# 飞书 API 返回的 JSON 不转义中文
_dumps = partial(json.dumps, ensure_ascii=False)
from unittest.mock import MagicMock, patch

import pytest

from feishu_bridge.test_client import FeishuTestClient


def _controlled_time(*values: float):
    """返回可控的 time.time 替身，超出序列后返回最后一个值。"""
    it = iter(values)
    last = values[-1] if values else 0

    def _time():
        nonlocal last
        try:
            last = next(it)
        except StopIteration:
            pass
        return last

    return _time


def _make_client() -> FeishuTestClient:
    """构造 mock 化的 FeishuTestClient。"""
    client = FeishuTestClient(app_id="test", app_secret="test")
    client.client = MagicMock()
    return client


# ── BEH-1: assert_message_edited ─────────────────────────────────────


def test_assert_message_edited_detects_change():
    """BEH-1: get_message 返回更新后 content → 成功。"""
    client = _make_client()

    old_result = {"message_id": "om_1", "content": _dumps({"text": "旧消息"}), "update_time": "1000"}
    new_result = {"message_id": "om_1", "content": _dumps({"text": "新的套餐详情"}), "update_time": "2000"}

    client.get_message = MagicMock(side_effect=[old_result, new_result])

    with patch("feishu_bridge.test_client.time.sleep"), \
         patch("feishu_bridge.test_client.time.time", _controlled_time(0, 0, 5)):
        result = client.assert_message_edited("oc_xxx", "om_1", "套餐", timeout=10)
    assert "套餐" in result["content"]


def test_assert_message_edited_timeout():
    """BEH-1: 超时未变化 → AssertionError。"""
    client = _make_client()

    old_result = {"message_id": "om_1", "content": _dumps({"text": "旧消息"}), "update_time": "1000"}
    client.get_message = MagicMock(return_value=old_result)

    with patch("feishu_bridge.test_client.time.sleep"), \
         patch("feishu_bridge.test_client.time.time", _controlled_time(0, 0, 5, 11)):
        with pytest.raises(AssertionError, match="did not contain"):
            client.assert_message_edited("oc_xxx", "om_1", "新内容", timeout=10)


# ── BEH-2: assert_card_appears ───────────────────────────────────────


def test_assert_card_appears_finds_interactive():
    """BEH-2: list_messages 返回 interactive 消息 → 匹配成功。"""
    client = _make_client()

    messages = [
        {"message_id": "om_text", "msg_type": "text", "content": "纯文本", "create_time": "1000", "root_id": None},
        {"message_id": "om_card", "msg_type": "interactive", "content": _dumps({"header": {"title": "进行中"}}), "create_time": "1001", "root_id": None},
    ]
    client.list_messages = MagicMock(return_value=messages)

    with patch("feishu_bridge.test_client.time.sleep"), \
         patch("feishu_bridge.test_client.time.time", _controlled_time(0, 0)):
        result = client.assert_card_appears("oc_xxx", "进行中", timeout=10)
    assert result["msg_type"] == "interactive"
    assert result["message_id"] == "om_card"


def test_assert_card_appears_timeout_no_card():
    """BEH-2: 无 interactive 消息 → 超时。"""
    client = _make_client()

    messages = [
        {"message_id": "om_text", "msg_type": "text", "content": "纯文本", "create_time": "1000", "root_id": None},
    ]
    client.list_messages = MagicMock(return_value=messages)

    with patch("feishu_bridge.test_client.time.sleep"), \
         patch("feishu_bridge.test_client.time.time", _controlled_time(0, 0, 5, 11)):
        with pytest.raises(AssertionError, match="Card containing"):
            client.assert_card_appears("oc_xxx", "进行中", timeout=10)


# ── BEH-3: assert_card_updated ───────────────────────────────────────


def test_assert_card_updated_detects_change():
    """BEH-3: card 内容更新后包含目标文本 → 成功。"""
    client = _make_client()

    messages = [
        {"message_id": "om_card", "msg_type": "interactive", "content": _dumps({"status": "进行中"}), "create_time": "1000", "root_id": None},
    ]
    client.list_messages = MagicMock(return_value=messages)

    updated = {"message_id": "om_card", "msg_type": "interactive", "content": _dumps({"status": "takeover"}), "update_time": "2000"}
    client.get_message = MagicMock(return_value=updated)

    with patch("feishu_bridge.test_client.time.sleep"), \
         patch("feishu_bridge.test_client.time.time", _controlled_time(0, 0)):
        result = client.assert_card_updated("oc_xxx", "takeover", timeout=10)
    assert "takeover" in result["content"]


# ── BEH-4: send_thread_reply ─────────────────────────────────────────


def test_send_thread_reply_calls_reply_api():
    """BEH-4: 调用 im.v1.message.reply + reply_in_thread=True。"""
    client = _make_client()

    resp = MagicMock(success=lambda: True)
    resp.data.message_id = "om_thread_reply"
    client.client.im.v1.message.reply.return_value = resp

    msg_id = client.send_thread_reply("oc_xxx", "om_root", "thread 消息")
    assert msg_id == "om_thread_reply"
    client.client.im.v1.message.reply.assert_called_once()


def test_send_thread_reply_failure_raises():
    """BEH-4: API 失败 → RuntimeError。"""
    client = _make_client()

    resp = MagicMock(success=lambda: False, code=99999, msg="not found")
    client.client.im.v1.message.reply.return_value = resp

    with pytest.raises(RuntimeError, match="send_thread_reply failed"):
        client.send_thread_reply("oc_xxx", "om_root", "消息")


# ── BEH-5: assert_thread_message_appears ─────────────────────────────


def test_assert_thread_message_appears_filters_by_root_id():
    """BEH-5: 按 root_id 过滤 + contains 匹配。"""
    client = _make_client()

    messages = [
        {"message_id": "om_a", "msg_type": "text", "content": "建议文本", "create_time": "1000", "root_id": None},
        {"message_id": "om_b", "msg_type": "text", "content": "thread 建议", "create_time": "1001", "root_id": "om_root_card"},
    ]
    client.list_messages = MagicMock(return_value=messages)

    with patch("feishu_bridge.test_client.time.sleep"), \
         patch("feishu_bridge.test_client.time.time", _controlled_time(0, 0)):
        result = client.assert_thread_message_appears(
            "oc_xxx", "om_root_card", "建议", timeout=10
        )
    assert result["root_id"] == "om_root_card"
    assert "建议" in result["content"]


def test_assert_thread_message_appears_timeout():
    """BEH-5: thread 中无匹配消息 → 超时。"""
    client = _make_client()

    client.list_messages = MagicMock(return_value=[])

    with patch("feishu_bridge.test_client.time.sleep"), \
         patch("feishu_bridge.test_client.time.time", _controlled_time(0, 0, 5, 11)):
        with pytest.raises(AssertionError, match="Thread message"):
            client.assert_thread_message_appears(
                "oc_xxx", "om_root", "找不到", timeout=10
            )


# ── BEH-6: send_message_as_operator ──────────────────────────────────


def test_send_message_as_operator_delegates():
    """BEH-6: 委托 send_message。"""
    client = _make_client()

    resp = MagicMock(success=lambda: True)
    resp.data.message_id = "om_op"
    client.client.im.v1.message.create.return_value = resp

    msg_id = client.send_message_as_operator("oc_xxx", "operator 消息")
    assert msg_id == "om_op"
    client.client.im.v1.message.create.assert_called_once()


# ── BEH-7 (partial): click_card_action ───────────────────────────────


def test_click_card_action_stores_payload():
    """click_card_action 构造并存储 card action payload。"""
    client = _make_client()
    client.click_card_action("oc_xxx", "5", conv_id="conv_42")

    assert client._last_card_action_payload == {
        "action": {"value": {"score": "5", "conv_id": "conv_42"}}
    }


def test_click_card_action_default_conv_id():
    """click_card_action conv_id 默认为空。"""
    client = _make_client()
    client.click_card_action("oc_xxx", "3")

    assert client._last_card_action_payload["action"]["value"]["conv_id"] == ""
