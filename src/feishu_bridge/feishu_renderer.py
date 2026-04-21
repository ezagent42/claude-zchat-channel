"""飞书 card 渲染器 — 纯函数，输入数据 → 输出 dict。

从 visibility_router.py 提取，专注于飞书 interactive card 的 JSON 构建：
- build_conv_card()  — conversation 状态卡片（thread root / mode 变更 / 关闭）
- csat_card()        — CSAT 评分卡片
"""

from __future__ import annotations


_STATE_LABELS = {
    "active": "进行中",
    "closed": "已关闭",
    "help_requested": "🚨 求助中",
    "help_timeout": "⚠️ 求助超时",
}

_MODE_LABELS = {
    "fast": "快速应答",
    "copilot": "Copilot",
    "takeover": "人工接管",
    "help": "等待人工",
}

_TEMPLATE_BY_STATE = {
    "closed": "red",
    "help_requested": "orange",
    "help_timeout": "red",
}


def build_conv_card(
    conversation_id: str,
    metadata: dict,
    mode: str = "fast",
    state: str = "active",
) -> dict:
    """构建 conversation card。

    Title 优先用 `metadata["chat_name"]`（友好群名）+ conv id + 状态；fallback 到 conv id。
    `metadata["alert"]` 出现时在 elements 顶部加醒目提示。
    state 决定 header 颜色：closed/help_timeout=red, help_requested=orange, 其它=blue。
    """
    state_label = _STATE_LABELS.get(state, state)
    mode_label = _MODE_LABELS.get(mode, mode)
    chat_name = metadata.get("chat_name") or ""
    customer = metadata.get("customer") or {}
    customer_name = customer.get("name") or customer.get("id") or "-"

    # title 优先用飞书群名（用户面友好）；fallback conv_id（调试/回溯定位）
    display_name = chat_name or conversation_id
    title = f"对话 {display_name} · {state_label}"

    elements: list[dict] = []

    alert = metadata.get("alert")
    if alert:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{alert}**"},
        })

    fields_md_lines = [
        f"**模式**：{mode_label}",
        f"**客户**：{customer_name}",
    ]
    resolution = metadata.get("resolution")
    if resolution:
        outcome = resolution.get("outcome", "-")
        fields_md_lines.append(f"**结果**：{outcome}")
        if resolution.get("csat_score") is not None:
            fields_md_lines.append(f"**CSAT**：{resolution['csat_score']}")

    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "\n".join(fields_md_lines)},
    })

    # 未关闭时才展示操作按钮（按 mode 切换 接管/释放）
    if state != "closed":
        if mode == "takeover":
            actions = [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "释放"},
                    "type": "primary",
                    "value": {"action": "release", "conv_id": conversation_id},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "结案"},
                    "value": {"action": "resolve", "conv_id": conversation_id},
                },
            ]
        else:
            actions = [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "接管"},
                    "value": {"action": "hijack", "conv_id": conversation_id},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "结案"},
                    "value": {"action": "resolve", "conv_id": conversation_id},
                },
            ]
        elements.append({"tag": "action", "actions": actions})

    return {
        # update_multi 必须为 true 才能让 update_card (patch) 对所有查看者生效；
        # 缺失时 PATCH 返回 200 但 UI 不刷新（静默失败的常见坑）。
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
        },
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": _TEMPLATE_BY_STATE.get(state, "blue"),
        },
        "elements": elements,
    }


def thank_you_card(score: int) -> dict:
    """CSAT 点击后 PATCH 成的卡：感谢评分，无按钮。

    保持和 csat_card 同样的 shape（无 header.template, 无 extra config 键），
    只在 header title 改文案 + elements 替换为一个 div 文本。飞书 PATCH 对
    card shape 大改兼容度不稳定，最小 diff 最可靠。
    """
    stars = "⭐" * max(1, min(score, 5))
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {
                "content": f"感谢您的评价 {stars}",
                "tag": "plain_text",
            },
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "plain_text", "content": f"您的评分：{score}/5"},
            },
        ],
    }


def csat_card(conversation_id: str) -> dict:
    """生成 CSAT 评分卡片。"""
    return {
        # update_multi 允许点击后（将来要换 card 展示"感谢评价"）对所有客户可见
        "config": {
            "wide_screen_mode": True,
            "update_multi": True,
        },
        "header": {
            "title": {"content": "请为本次服务评分", "tag": "plain_text"}
        },
        "elements": [
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"content": f"{'⭐' * i}", "tag": "plain_text"},
                        "value": {"score": str(i), "conv_id": conversation_id},
                    }
                    for i in range(1, 6)
                ],
            }
        ],
    }
