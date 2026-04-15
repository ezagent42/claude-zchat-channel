"""可插拔消息类型解析器 — 从 cc-openclaw 移植，去掉 server 依赖改为 bridge 参数。

每个 parser 通过 @register_parser("msg_type") 注册。
签名: (content: dict, message, bridge) -> tuple[str, str]
返回 (text, file_path)。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from feishu_bridge.bridge import FeishuBridge

log = logging.getLogger("feishu-bridge.parsers")

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_parsers: dict[str, Callable] = {}


def register_parser(*msg_types: str):
    """装饰器：为一个或多个消息类型注册解析器。"""
    def decorator(fn):
        for mt in msg_types:
            _parsers[mt] = fn
        return fn
    return decorator


def parse_message(
    msg_type: str,
    content: dict,
    message,
    bridge: FeishuBridge | None,
) -> tuple[str, str]:
    """解析飞书消息为 (text, file_path)。未注册类型返回描述性 fallback。"""
    parser = _parsers.get(msg_type)
    if parser:
        try:
            return parser(content, message, bridge)
        except Exception as e:
            log.warning("Parser for %s failed: %s", msg_type, e)
            return f"[{msg_type} 消息 — 解析失败]", ""
    return f"[{msg_type} 消息]", ""


# ---------------------------------------------------------------------------
# P0: text, post
# ---------------------------------------------------------------------------

@register_parser("text")
def _parse_text(content: dict, message, bridge) -> tuple[str, str]:
    return content.get("text", ""), ""


@register_parser("post")
def _parse_post(content: dict, message, bridge) -> tuple[str, str]:
    parts = [content.get("title", "")]
    for para in content.get("content", []):
        for node in para or []:
            if node.get("text"):
                parts.append(node["text"])
    return " ".join(p for p in parts if p), ""


# ---------------------------------------------------------------------------
# P0: downloadable files (image, file, audio, media)
# ---------------------------------------------------------------------------

@register_parser("image", "file", "audio", "media")
def _parse_downloadable(content: dict, message, bridge) -> tuple[str, str]:
    if bridge is None:
        msg_type = "image"
        if message and hasattr(message, "message_type"):
            msg_type = message.message_type or "file"
        file_key = content.get("image_key", "") or content.get("file_key", "")
        return f"[{msg_type} — 下载需要 bridge 实例, key={file_key}]", ""

    msg_id = message.message_id if message else ""
    file_path = bridge.download_file(msg_id, message)
    msg_type = message.message_type if message else "file"
    if file_path:
        return f"[File received: {file_path}]", file_path
    return f"[{msg_type} — 下载失败]", ""


# ---------------------------------------------------------------------------
# P0: interactive (card), merge_forward
# ---------------------------------------------------------------------------

@register_parser("interactive")
def _parse_interactive(content: dict, message, bridge) -> tuple[str, str]:
    """提取 interactive card 的文本内容。"""
    parts: list[str] = []

    if isinstance(content, list):
        elements = content
    else:
        header = content.get("header", {})
        if isinstance(header, dict):
            title = header.get("title", {})
            if isinstance(title, dict):
                parts.append(title.get("content", ""))
            elif isinstance(title, str):
                parts.append(title)
        elements = content.get("elements", [])
        if not isinstance(elements, list):
            elements = []

    def _extract(nodes: list):
        for node in nodes:
            if isinstance(node, list):
                _extract(node)
                continue
            if not isinstance(node, dict):
                continue
            tag = node.get("tag", "")
            if tag == "text":
                t = node.get("text", "")
                if t:
                    parts.append(t)
            elif tag == "div":
                text_obj = node.get("text", {})
                if isinstance(text_obj, dict):
                    parts.append(text_obj.get("content", ""))
                elif isinstance(text_obj, str):
                    parts.append(text_obj)
                fields = node.get("fields", [])
                if fields:
                    _extract(fields)
            elif tag == "markdown":
                parts.append(node.get("content", ""))
            elif tag == "note":
                _extract(node.get("elements", []))
            elif tag == "action":
                for action in node.get("actions", []):
                    if not isinstance(action, dict):
                        continue
                    action_text = action.get("text", {})
                    if isinstance(action_text, dict):
                        parts.append(f"[按钮: {action_text.get('content', '')}]")
                    elif isinstance(action_text, str):
                        parts.append(f"[按钮: {action_text}]")
            elif tag == "hr":
                parts.append("---")
            elif tag == "a":
                parts.append(node.get("text", "") or node.get("href", ""))
            elif tag == "at":
                parts.append(
                    f"@{node.get('user_name', node.get('user_id', ''))}"
                )

    _extract(elements)

    text = "\n".join(p for p in parts if p)
    if text and "请升级至最新版本客户端" in text:
        return "[消息卡片 — 内容不可通过 API 获取]", ""
    return text or "[消息卡片]", ""


@register_parser("merge_forward")
def _parse_merge_forward(content: dict, message, bridge) -> tuple[str, str]:
    if not message or not bridge:
        return "[合并转发消息]", ""

    msg_id = getattr(message, "message_id", "") or ""
    if not msg_id:
        return "[合并转发消息]", ""

    try:
        from lark_oapi.api.im.v1 import GetMessageRequest

        req = GetMessageRequest.builder().message_id(msg_id).build()
        resp = bridge._client.im.v1.message.get(req)

        if not resp.success() or not resp.data or not resp.data.items:
            return "[合并转发消息 — 获取失败]", ""

        lines = ["--- 合并转发 ---"]
        for item in resp.data.items:
            sub_type = item.msg_type or ""
            if sub_type == "merge_forward":
                continue
            raw_content = item.body.content if item.body and item.body.content else ""
            try:
                sub_content = json.loads(raw_content) if raw_content else {}
            except Exception:
                sub_content = {}
            sub_text, _ = parse_message(sub_type, sub_content, item, bridge)
            sender_name = ""
            if item.sender and item.sender.id:
                sender_name = item.sender.id
            if sub_text:
                prefix = f"{sender_name}: " if sender_name else ""
                lines.append(f"{prefix}{sub_text}")
        lines.append("--- 合并转发结束 ---")
        return "\n".join(lines), ""
    except Exception as e:
        log.warning("merge_forward parse error: %s", e)
        return "[合并转发消息 — 解析失败]", ""


# ---------------------------------------------------------------------------
# P1: sticker, share_chat, share_user, location, todo
# ---------------------------------------------------------------------------

@register_parser("sticker")
def _parse_sticker(content: dict, message, bridge) -> tuple[str, str]:
    return "[表情包]", ""


@register_parser("share_chat")
def _parse_share_chat(content: dict, message, bridge) -> tuple[str, str]:
    chat_id = content.get("chat_id", "")
    return f"[群名片: {chat_id}]", ""


@register_parser("share_user")
def _parse_share_user(content: dict, message, bridge) -> tuple[str, str]:
    user_id = content.get("user_id", "")
    return f"[用户名片: {user_id}]", ""


@register_parser("location")
def _parse_location(content: dict, message, bridge) -> tuple[str, str]:
    name = content.get("name", "未知位置")
    lat = content.get("latitude", "")
    lng = content.get("longitude", "")
    coords = f" ({lat}, {lng})" if lat and lng else ""
    return f"[位置: {name}{coords}]", ""


@register_parser("todo")
def _parse_todo(content: dict, message, bridge) -> tuple[str, str]:
    task_id = content.get("task_id", "")
    summary = content.get("summary", "")

    if isinstance(summary, dict):
        parts = []
        for para in summary.get("content", []):
            for node in para or []:
                if node.get("text"):
                    parts.append(node["text"])
        summary = " ".join(parts)
    elif isinstance(summary, str) and summary.startswith("{"):
        try:
            parsed = json.loads(summary)
            parts = []
            for para in parsed.get("content", []):
                for node in para or []:
                    if node.get("text"):
                        parts.append(node["text"])
            summary = " ".join(parts)
        except Exception:
            pass

    if summary:
        return f"[任务: {summary}]", ""
    if task_id:
        return f"[任务: task_id={task_id}]", ""
    return "[任务消息]", ""


# ---------------------------------------------------------------------------
# P2: system, hongbao, vote, video_chat, calendar, folder
# ---------------------------------------------------------------------------

@register_parser("system")
def _parse_system(content: dict, message, bridge) -> tuple[str, str]:
    template = content.get("template", "")
    if "add_member" in template or "join" in template:
        return "[系统: 新成员加入]", ""
    if "remove_member" in template or "leave" in template:
        return "[系统: 成员退出]", ""
    if "rename" in template:
        return "[系统: 群名变更]", ""
    if "divider" in template:
        divider = content.get("divider_text", {})
        if isinstance(divider, dict):
            text = divider.get("zh_cn", "") or divider.get("en_us", "") or str(divider)
        else:
            text = str(divider)
        return f"[系统: {text}]" if text else "[系统消息]", ""
    return f"[系统消息: {template}]" if template else "[系统消息]", ""


@register_parser("hongbao")
def _parse_hongbao(content: dict, message, bridge) -> tuple[str, str]:
    return "[红包]", ""


@register_parser("vote")
def _parse_vote(content: dict, message, bridge) -> tuple[str, str]:
    topic = content.get("topic", "")
    options = content.get("options", [])
    if topic:
        opt_text = " / ".join(options) if options else ""
        return f"[投票: {topic}] {opt_text}".strip(), ""
    return "[投票]", ""


@register_parser("video_chat")
def _parse_video_chat(content: dict, message, bridge) -> tuple[str, str]:
    topic = content.get("topic", "视频通话")
    return f"[视频通话: {topic}]", ""


@register_parser("share_calendar_event", "calendar", "general_calendar")
def _parse_calendar(content: dict, message, bridge) -> tuple[str, str]:
    summary = content.get("summary", "")
    return f"[日历: {summary}]" if summary else "[日历事件]", ""


@register_parser("folder")
def _parse_folder(content: dict, message, bridge) -> tuple[str, str]:
    file_name = content.get("file_name", "")
    return f"[文件夹: {file_name}]" if file_name else "[文件夹]", ""
