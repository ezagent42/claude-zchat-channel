"""Visibility → 飞书群路由（card + thread 模型）。

每个 conversation 在 squad 群内以 interactive card 作为 thread root，
后续 public/side/system 消息以 thread 回复的形式追加。

- conversation.created → 在 squad 群发 card（thread root）
- public reply → 双写：send_text(customer_chat) + reply_in_thread(squad)
- side → 仅 reply_in_thread(squad)
- system → reply_in_thread(squad) + 可选 send_text(admin)
- mode.changed → update_card(card_msg_id, 新状态)
- conversation.closed → update_card(card_msg_id, state=closed, resolution)
- edit（客户可见消息） → 通过 msg_id_map 查到 feishu_msg_id → update_message
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from feishu_bridge.group_manager import GroupManager
    from feishu_bridge.sender import FeishuSender

log = logging.getLogger("feishu-bridge.visibility_router")


@dataclass
class ConvThread:
    """跟踪一个 conversation 在 squad 群的 card+thread 状态。"""

    conversation_id: str
    squad_chat_id: str
    card_msg_id: str | None = None  # thread root（interactive card）
    customer_chat_id: str | None = None
    mode: str = "fast"
    state: str = "active"  # "active" / "closed"
    metadata: dict = field(default_factory=dict)


class VisibilityRouter:
    """根据 visibility 将消息路由到对应飞书群（card+thread 模型）。"""

    def __init__(
        self,
        sender: FeishuSender,
        group_manager: GroupManager,
        admin_chat_id: str | None = None,
    ) -> None:
        self.sender = sender
        self.group_manager = group_manager
        self.admin_chat_id = admin_chat_id
        # conv_id → ConvThread
        self._threads: dict[str, ConvThread] = {}
        # cs_msg_id → feishu_msg_id（供 edit 事件查映射使用）
        self._msg_id_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 状态查询（供测试 / 外部使用）
    # ------------------------------------------------------------------

    def get_thread(self, conversation_id: str) -> ConvThread | None:
        return self._threads.get(conversation_id)

    def get_feishu_msg_id(self, cs_msg_id: str) -> str | None:
        return self._msg_id_map.get(cs_msg_id)

    # ------------------------------------------------------------------
    # Lifecycle：conversation.created
    # ------------------------------------------------------------------

    def on_conversation_created(
        self,
        conversation_id: str,
        metadata: dict | None = None,
    ) -> str | None:
        """在 squad 群发 interactive card 作为 thread root。

        Returns: card_msg_id（或 None，如 squad 群未配置 / send 失败）。
        """
        squad_chat = self.group_manager.get_squad_chat(conversation_id)
        customer_chat = self.group_manager.get_customer_chat(conversation_id)
        if not squad_chat:
            log.warning("on_conversation_created: no squad chat for %s", conversation_id)
            return None
        meta = dict(metadata or {})
        card = self._build_conv_card(conversation_id, meta, mode="fast", state="active")
        card_msg_id = self.sender.send_card(squad_chat, card)
        thread = ConvThread(
            conversation_id=conversation_id,
            squad_chat_id=squad_chat,
            card_msg_id=card_msg_id,
            customer_chat_id=customer_chat,
            metadata=meta,
        )
        self._threads[conversation_id] = thread
        return card_msg_id

    # ------------------------------------------------------------------
    # 消息路由
    # ------------------------------------------------------------------

    def route(self, conversation_id: str, message: dict) -> str | None:
        """根据 visibility 路由消息。

        public → send_text(customer) + reply_in_thread(squad)
        side   → reply_in_thread(squad)
        system → reply_in_thread(squad) [+ send_text(admin)]

        如果 message 带 message_id（cs 侧 id），将面向客户的 feishu_msg_id 存入 msg_id_map
        供后续 edit 事件查找。

        Returns: 面向客户的 feishu_msg_id（public 时），否则 None。
        """
        visibility = message.get("visibility", "public")
        text = message.get("text", "")
        cs_msg_id = message.get("message_id")

        thread = self._threads.get(conversation_id)
        customer_chat = (
            thread.customer_chat_id
            if thread is not None
            else self.group_manager.get_customer_chat(conversation_id)
        )

        customer_facing_msg_id: str | None = None

        if visibility == "public":
            if customer_chat:
                mid = self.sender.send_text(customer_chat, text)
                if mid:
                    customer_facing_msg_id = mid
                    if cs_msg_id:
                        self._msg_id_map[cs_msg_id] = mid
            if thread and thread.card_msg_id:
                self.sender.reply_in_thread(thread.card_msg_id, f"[→客户] {text}")

        elif visibility == "side":
            if thread and thread.card_msg_id:
                self.sender.reply_in_thread(thread.card_msg_id, f"[侧栏] {text}")

        elif visibility == "system":
            if thread and thread.card_msg_id:
                self.sender.reply_in_thread(thread.card_msg_id, f"[系统] {text}")
            if self.admin_chat_id:
                self.sender.send_text(self.admin_chat_id, f"[系统] {text}")

        # CSAT 评分卡片（复用原有逻辑）
        if message.get("type") == "csat_request" and customer_chat:
            self.sender.send_card(customer_chat, self._csat_card(conversation_id))

        return customer_facing_msg_id

    # ------------------------------------------------------------------
    # 编辑（客户可见消息）
    # ------------------------------------------------------------------

    def on_edit(self, conversation_id: str, cs_msg_id: str, text: str) -> bool:
        """收到 cs 侧 edit 事件 → 查 msg_id 映射 → update_message。

        同时在 squad thread 追加一条 [edited] 标记，方便 operator 看到修改记录。
        """
        feishu_msg_id = self._msg_id_map.get(cs_msg_id)
        edited_ok = False
        if feishu_msg_id:
            edited_ok = bool(self.sender.update_message(feishu_msg_id, text))
        thread = self._threads.get(conversation_id)
        if thread and thread.card_msg_id:
            self.sender.reply_in_thread(thread.card_msg_id, f"[edited] {text}")
        return edited_ok

    # ------------------------------------------------------------------
    # Lifecycle：mode.changed / conversation.closed
    # ------------------------------------------------------------------

    def on_mode_changed(self, conversation_id: str, mode: str, **_kwargs: Any) -> bool:
        """模式切换（fast/copilot/takeover）→ 刷新 card。"""
        thread = self._threads.get(conversation_id)
        if not thread or not thread.card_msg_id:
            return False
        thread.mode = mode
        card = self._build_conv_card(
            conversation_id, thread.metadata, mode=mode, state=thread.state
        )
        return bool(self.sender.update_card(thread.card_msg_id, card))

    def on_conversation_closed(
        self,
        conversation_id: str,
        resolution: dict | None = None,
    ) -> bool:
        """结案 → 刷新 card 为 closed 状态。"""
        thread = self._threads.get(conversation_id)
        if not thread or not thread.card_msg_id:
            return False
        thread.state = "closed"
        if resolution:
            thread.metadata["resolution"] = resolution
        card = self._build_conv_card(
            conversation_id, thread.metadata, mode=thread.mode, state="closed"
        )
        return bool(self.sender.update_card(thread.card_msg_id, card))

    # ------------------------------------------------------------------
    # Card 构建
    # ------------------------------------------------------------------

    def _build_conv_card(
        self,
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

    def _csat_card(self, conversation_id: str) -> dict:
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
