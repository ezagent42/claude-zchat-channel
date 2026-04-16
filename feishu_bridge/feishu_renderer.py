"""飞书 card 渲染器 — 纯函数，输入数据 → 输出 dict。

从 visibility_router.py 提取，专注于飞书 interactive card 的 JSON 构建：
- build_conv_card()  — conversation 状态卡片（thread root / mode 变更 / 关闭）
- csat_card()        — CSAT 评分卡片
"""

from __future__ import annotations


def build_conv_card(
    conversation_id: str,
    metadata: dict,
    mode: str = "fast",
    state: str = "active",
) -> dict:
    """构建 conversation card：header 显示 conv_id + 状态，elements 包含元信息 + 操作按钮。"""
    state_label = {"active": "进行中", "closed": "已关闭"}.get(state, state)
    mode_label = {
        "fast": "快速应答",
        "copilot": "Copilot",
        "takeover": "人工接管",
    }.get(mode, mode)
    customer = metadata.get("customer") or {}
    customer_name = customer.get("name") or customer.get("id") or "-"

    title = f"对话 {conversation_id} · {state_label}"

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

    elements: list[dict] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "\n".join(fields_md_lines),
            },
        }
    ]

    # 未关闭时才展示操作按钮（纯展示，交互处理在 cs 侧）
    if state != "closed":
        elements.append(
            {
                "tag": "action",
                "actions": [
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
                ],
            }
        )

    return {
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "red" if state == "closed" else "blue",
        },
        "elements": elements,
    }


def csat_card(conversation_id: str) -> dict:
    """生成 CSAT 评分卡片。"""
    return {
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
