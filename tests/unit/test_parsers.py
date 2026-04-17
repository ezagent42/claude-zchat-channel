"""TC-001 ~ TC-008: message_parsers 单元测试。

覆盖 text / post / image / interactive / sticker / unknown / location / system
8 种消息类型的解析逻辑。
"""

from feishu_bridge.message_parsers import parse_message


def test_parse_text():
    """TC-001: text 消息 → 直接返回文本内容。"""
    text, path = parse_message("text", {"text": "hello"}, None, None)
    assert text == "hello"
    assert path == ""


def test_parse_post():
    """TC-002: post 富文本 → 拼接 title + 段落文本。"""
    content = {
        "title": "标题",
        "content": [[{"text": "段落1"}], [{"text": "段落2"}]],
    }
    text, _ = parse_message("post", content, None, None)
    assert "标题" in text
    assert "段落1" in text
    assert "段落2" in text


def test_parse_image_without_bridge():
    """TC-003: image 消息（无 bridge）→ 描述性 fallback。"""
    text, path = parse_message("image", {"image_key": "img_xxx"}, None, None)
    assert "image" in text.lower() or "File" in text or "下载" in text


def test_parse_interactive_card():
    """TC-004: interactive card → 提取 header + elements 文本。"""
    content = {
        "header": {"title": {"content": "评分", "tag": "plain_text"}},
        "elements": [
            {"tag": "div", "text": {"content": "请评分", "tag": "plain_text"}}
        ],
    }
    text, _ = parse_message("interactive", content, None, None)
    assert "评分" in text


def test_parse_sticker():
    """TC-005: sticker → [表情包] 标签。"""
    text, _ = parse_message("sticker", {}, None, None)
    assert "表情" in text


def test_parse_unknown_type():
    """TC-006: 未注册类型 → 包含类型名的 fallback。"""
    text, _ = parse_message("some_future_type", {}, None, None)
    assert "some_future_type" in text


def test_parse_location():
    """TC-007: location → 包含地名的位置标签。"""
    content = {"name": "星巴克", "latitude": "31.2", "longitude": "121.4"}
    text, _ = parse_message("location", content, None, None)
    assert "星巴克" in text


def test_parse_system():
    """TC-008: system 消息（add_member）→ 系统通知。"""
    text, _ = parse_message("system", {"template": "add_member"}, None, None)
    assert "加入" in text or "系统" in text
