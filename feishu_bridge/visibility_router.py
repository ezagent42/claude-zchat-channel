"""Visibility → 飞书群路由。

- public → customer 群 + squad 群
- side → 只发 squad 群
- system → squad 群 + admin 群
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from feishu_bridge.group_manager import GroupManager
    from feishu_bridge.sender import FeishuSender

log = logging.getLogger("feishu-bridge.visibility_router")


class VisibilityRouter:
    """根据 visibility 将消息路由到对应飞书群。"""

    def __init__(
        self,
        sender: FeishuSender,
        group_manager: GroupManager,
        admin_chat_id: str | None = None,
    ) -> None:
        self.sender = sender
        self.group_manager = group_manager
        self.admin_chat_id = admin_chat_id

    def route(self, conversation_id: str, message: dict) -> None:
        """根据 visibility 路由消息到对应飞书群。"""
        visibility = message.get("visibility", "public")
        text = message.get("text", "")

        customer_chat = self.group_manager.get_customer_chat(conversation_id)
        squad_chat = self.group_manager.get_squad_chat(conversation_id)

        if visibility == "public":
            if customer_chat:
                self.sender.send_text(customer_chat, text)
            if squad_chat:
                self.sender.send_text(squad_chat, f"[→客户] {text}")

        elif visibility == "side":
            if squad_chat:
                self.sender.send_text(squad_chat, f"[侧栏] {text}")

        elif visibility == "system":
            if squad_chat:
                self.sender.send_text(squad_chat, f"[系统] {text}")
            if self.admin_chat_id:
                self.sender.send_text(self.admin_chat_id, f"[系统] {text}")

        if message.get("type") == "csat_request" and customer_chat:
            self.sender.send_card(customer_chat, self._csat_card(conversation_id))

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
