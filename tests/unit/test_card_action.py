"""Unit tests: CardAwareClient + _on_card_action CSAT / hijack / resolve。

覆盖测试计划 TC-1 ~ TC-9：
- TC-1 test_card_aware_client_dispatches_card
- TC-2 test_event_frame_delegates_to_super
- TC-3 test_card_handler_exception_swallowed
- TC-4 test_card_action_extracts_score
- TC-5 test_card_action_sends_csat_to_bridge
- TC-6 test_card_action_missing_fields_noop
- TC-7 test_card_action_hijack_sends_operator_command
- TC-8 test_card_action_resolve_sends_operator_command
- TC-9 test_card_action_unknown_action_type_noop
"""

from __future__ import annotations

import asyncio
import http
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from feishu_bridge.ws_client import CardAwareClient


# ------------------------------------------------------------------ #
# 辅助：构造 mock Frame（模拟 protobuf Frame 对象）
# ------------------------------------------------------------------ #


def _make_header(key: str, value: str):
    """模拟 protobuf header entry。"""
    h = MagicMock()
    h.key = key
    h.value = value
    return h


class _MockHeaders:
    """模拟 protobuf RepeatedCompositeFieldContainer（headers 列表 + add 方法）。"""

    def __init__(self, items: list):
        self._items = list(items)

    def __iter__(self):
        return iter(self._items)

    def add(self):
        h = MagicMock()
        self._items.append(h)
        return h


def _make_frame(message_type: str, payload_dict: dict) -> MagicMock:
    """构造一个 DATA Frame mock，headers 包含必需字段。"""
    frame = MagicMock()
    headers = _MockHeaders([
        _make_header("type", message_type),
        _make_header("message_id", "msg_001"),
        _make_header("trace_id", "trace_001"),
        _make_header("sum", "1"),
        _make_header("seq", "0"),
    ])
    frame.headers = headers
    frame.payload = json.dumps(payload_dict).encode("utf-8")
    frame.SerializeToString.return_value = b"serialized"
    return frame


# ------------------------------------------------------------------ #
# TC-1: CARD 帧 → card_handler 被调用
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_card_aware_client_dispatches_card() -> None:
    """TC-1: CARD 帧 → card_handler 被调用，payload 正确。"""
    handler = MagicMock()
    card_payload = {"action": {"value": {"score": "5", "conv_id": "c1"}}}
    frame = _make_frame("card", card_payload)

    client = CardAwareClient.__new__(CardAwareClient)
    client._card_handler = handler
    client._cache = MagicMock()
    client._lock = asyncio.Lock()
    client._conn = MagicMock()
    client._conn.send = AsyncMock()

    await client._handle_data_frame(frame)

    handler.assert_called_once()
    actual_payload = handler.call_args[0][0]
    assert actual_payload["action"]["value"]["score"] == "5"
    assert actual_payload["action"]["value"]["conv_id"] == "c1"


# ------------------------------------------------------------------ #
# TC-2: EVENT 帧 → super() 原逻辑
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_event_frame_delegates_to_super() -> None:
    """TC-2: EVENT 帧 → 走原 SDK 逻辑，card_handler 不被调用。"""
    handler = MagicMock()
    frame = _make_frame("event", {"type": "im.message.receive_v1"})

    client = CardAwareClient.__new__(CardAwareClient)
    client._card_handler = handler
    client._cache = MagicMock()
    client._lock = asyncio.Lock()
    client._conn = MagicMock()
    client._conn.send = AsyncMock()

    with patch.object(
        CardAwareClient.__bases__[0], "_handle_data_frame", new_callable=AsyncMock
    ) as mock_super:
        await client._handle_data_frame(frame)

    handler.assert_not_called()
    mock_super.assert_called_once_with(frame)


# ------------------------------------------------------------------ #
# TC-3: handler 抛异常 → 连接不断，Response 500
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_card_handler_exception_swallowed() -> None:
    """TC-3: handler 抛异常 → 连接不断，写回 500 Response。"""
    handler = MagicMock(side_effect=ValueError("boom"))
    frame = _make_frame("card", {"action": {"value": {"score": "3"}}})

    client = CardAwareClient.__new__(CardAwareClient)
    client._card_handler = handler
    client._cache = MagicMock()
    client._lock = asyncio.Lock()
    client._conn = MagicMock()
    client._conn.send = AsyncMock()

    # 不应抛异常
    await client._handle_data_frame(frame)

    # 验证 Response 被写回（frame.payload 被设置 + SerializeToString 被调用）
    handler.assert_called_once()
    assert frame.payload is not None
    # 解析写回的 response
    resp_data = json.loads(frame.payload.decode("utf-8") if isinstance(frame.payload, bytes) else frame.payload)
    assert resp_data["code"] == http.HTTPStatus.INTERNAL_SERVER_ERROR


# ------------------------------------------------------------------ #
# TC-4: 解析 action.value
# ------------------------------------------------------------------ #


def test_card_action_extracts_score() -> None:
    """TC-4: payload 解析出 score=4, conv_id="c1"。"""
    from feishu_bridge.bridge import FeishuBridge

    bridge = FeishuBridge.__new__(FeishuBridge)
    bridge._bridge_ws = None  # 不实际连接

    payload = {"action": {"value": {"score": "4", "conv_id": "c1"}}}
    score, conv_id = bridge._parse_card_action(payload)
    assert score == 4
    assert conv_id == "c1"


# ------------------------------------------------------------------ #
# TC-5: 解析后发送 csat 到 Bridge API
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_card_action_sends_csat_to_bridge() -> None:
    """TC-5: 解析后通过 Bridge API 发送 V4 message（__csat_score: 编码）。"""
    from feishu_bridge.bridge import FeishuBridge

    bridge = FeishuBridge.__new__(FeishuBridge)
    mock_client = MagicMock()
    mock_client.connected = True
    bridge._bridge_client = mock_client

    # bridge needs sender + outbound stubs for thank-you card
    bridge.sender = MagicMock()
    bridge.outbound = MagicMock()
    bridge.outbound.pop_csat_card_msg_id.return_value = ""

    payload = {"action": {"value": {"score": "3", "conv_id": "conv_42"}}, "card_msg_id": ""}
    bridge._on_card_action(payload)

    mock_client.send.assert_called_once()
    sent = mock_client.send.call_args[0][0]
    # V6+: CSAT 走 event 通道（不污染 message/IRC 路径）
    assert sent["type"] == "event"
    assert sent["event"] == "csat_score"
    assert sent["channel"] == "conv_42"
    assert sent["data"]["score"] == 3
    assert sent["data"]["source"] == "customer"


# ------------------------------------------------------------------ #
# TC-6: 缺 score 或 conv_id → noop
# ------------------------------------------------------------------ #


def test_card_action_missing_fields_noop() -> None:
    """TC-6: 缺 score 或 conv_id → 不发送，不报错。"""
    from feishu_bridge.bridge import FeishuBridge

    bridge = FeishuBridge.__new__(FeishuBridge)
    mock_client = MagicMock()
    mock_client.connected = True
    bridge._bridge_client = mock_client

    # 缺 score
    bridge._on_card_action({"action": {"value": {"conv_id": "c1"}}})
    mock_client.send.assert_not_called()

    # 缺 conv_id
    bridge._on_card_action({"action": {"value": {"score": "5"}}})
    mock_client.send.assert_not_called()

    # action 结构异常
    bridge._on_card_action({"foo": "bar"})
    mock_client.send.assert_not_called()

    # 空 payload
    bridge._on_card_action({})
    mock_client.send.assert_not_called()


# ------------------------------------------------------------------ #
# TC-7: hijack 按钮 → operator_command
# ------------------------------------------------------------------ #


def test_card_action_hijack_sends_operator_command() -> None:
    """TC-7: hijack 按钮 → 发送 V4 message，content=/hijack，channel=conv_id。"""
    from feishu_bridge.bridge import FeishuBridge

    bridge = FeishuBridge.__new__(FeishuBridge)
    mock_client = MagicMock()
    mock_client.connected = True
    bridge._bridge_client = mock_client

    payload = {"action": {"value": {"action": "hijack", "conv_id": "oc_3e33"}}}
    bridge._on_card_action(payload)

    mock_client.send.assert_called_once()
    sent = mock_client.send.call_args[0][0]
    # V4：不再发 type=command；统一 type=message，content="/hijack"
    assert sent["type"] == "message"
    assert sent["channel"] == "oc_3e33"
    assert sent["content"] == "/hijack"
    assert sent["source"] == "card_action"


# ------------------------------------------------------------------ #
# TC-8: resolve 按钮 → operator_command
# ------------------------------------------------------------------ #


def test_card_action_resolve_sends_operator_command() -> None:
    """TC-8: resolve 按钮 → 发送 V4 message，content=/resolve，channel=conv_id。"""
    from feishu_bridge.bridge import FeishuBridge

    bridge = FeishuBridge.__new__(FeishuBridge)
    mock_client = MagicMock()
    mock_client.connected = True
    bridge._bridge_client = mock_client

    payload = {"action": {"value": {"action": "resolve", "conv_id": "oc_abc"}}}
    bridge._on_card_action(payload)

    mock_client.send.assert_called_once()
    sent = mock_client.send.call_args[0][0]
    # V4：统一 type=message，content="/resolve"
    assert sent["type"] == "message"
    assert sent["channel"] == "oc_abc"
    assert sent["content"] == "/resolve"
    assert sent["source"] == "card_action"


# ------------------------------------------------------------------ #
# TC-9: action 字段存在但缺 conv_id → noop
# ------------------------------------------------------------------ #


def test_card_action_unknown_action_type_noop() -> None:
    """TC-9: action 存在但缺 conv_id → 不发送。"""
    from feishu_bridge.bridge import FeishuBridge

    bridge = FeishuBridge.__new__(FeishuBridge)
    mock_client = MagicMock()
    mock_client.connected = True
    bridge._bridge_client = mock_client

    # action 存在但没有 conv_id
    bridge._on_card_action({"action": {"value": {"action": "hijack"}}})
    mock_client.send.assert_not_called()

    # conv_id 存在但 action 为空字符串（falsy）
    bridge._on_card_action({"action": {"value": {"action": "", "conv_id": "c1"}}})
    mock_client.send.assert_not_called()
