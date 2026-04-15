"""TC-020 ~ TC-022: sender 单元测试。

通过 mock lark client 验证 send_text / send_card / update_message
的 API 调用路径。
"""

from unittest.mock import MagicMock

from feishu_bridge.sender import FeishuSender


def test_send_text_calls_api():
    """TC-020: send_text_sync → im.v1.message.create 调用一次。"""
    sender = FeishuSender(app_id="test", app_secret="test")
    sender._client = MagicMock()
    sender._client.im.v1.message.create.return_value = MagicMock(
        success=lambda: True
    )
    sender.send_text_sync("oc_xxx", "hello")
    sender._client.im.v1.message.create.assert_called_once()


def test_send_card_calls_api():
    """TC-021: send_card_sync → im.v1.message.create（msg_type=interactive）。"""
    sender = FeishuSender(app_id="test", app_secret="test")
    sender._client = MagicMock()
    sender._client.im.v1.message.create.return_value = MagicMock(
        success=lambda: True
    )
    sender.send_card_sync("oc_xxx", {"header": {}, "elements": []})
    sender._client.im.v1.message.create.assert_called_once()


def test_update_message_calls_patch_api():
    """TC-022: update_message_sync → im.v1.message.patch 调用一次。"""
    sender = FeishuSender(app_id="test", app_secret="test")
    sender._client = MagicMock()
    sender._client.im.v1.message.patch.return_value = MagicMock(
        success=lambda: True
    )
    sender.update_message_sync("om_xxx", "updated text")
    sender._client.im.v1.message.patch.assert_called_once()
