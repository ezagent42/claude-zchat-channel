"""OutboundRouter — channel-server → 飞书出站路由（V4 协议）。

V4 变化：
- 不读 visibility 字段，改由 irc_encoding.parse(content).kind 在 bridge.py 中解析后传入
- route(conv_id, kind, text, cs_msg_id) 中：
    kind=msg/plain → 客户群 send_text + squad reply_in_thread
    kind=side      → 仅 squad reply_in_thread
    （kind=edit/sys 由 bridge.py 在调用前过滤）

每个 conversation 在 squad 群内以 interactive card 作为 thread root，
后续 msg/side 消息以 thread 回复的形式追加。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from feishu_bridge.feishu_renderer import build_conv_card, csat_card

if TYPE_CHECKING:
    from feishu_bridge.group_manager import GroupManager
    from feishu_bridge.sender import FeishuSender

log = logging.getLogger("feishu-bridge.outbound")


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


class OutboundRouter:
    """将出站消息（channel-server → 飞书）按 kind 路由到对应飞书群。"""

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

    def get_conversation_for_squad(self, squad_chat_id: str) -> str | None:
        """反向查找：squad chat_id → 最近活跃的 conversation_id。"""
        for conv_id, thread in self._threads.items():
            if thread.squad_chat_id == squad_chat_id and thread.state == "active":
                return conv_id
        return None

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
        card = build_conv_card(conversation_id, meta, mode="fast", state="active")
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
    # 消息路由（V4：按 kind 路由，不读 visibility 字段）
    # ------------------------------------------------------------------

    def route(
        self,
        conversation_id: str,
        *,
        kind: str,
        text: str,
        cs_msg_id: str | None = None,
    ) -> str | None:
        """按 kind 路由出站消息到飞书群。

        kind=msg/plain → send_text(customer) + reply_in_thread(squad)
        kind=side      → reply_in_thread(squad) 仅

        如果带 cs_msg_id，将面向客户的 feishu_msg_id 存入 msg_id_map 供 edit 查找。

        Returns: 面向客户的 feishu_msg_id（msg/plain 时），否则 None。
        """
        thread = self._threads.get(conversation_id)
        customer_chat = (
            thread.customer_chat_id
            if thread is not None
            else self.group_manager.get_customer_chat(conversation_id)
        )
        # Fallback: 飞书场景中 conversation_id 就是 customer chat_id
        if customer_chat is None and conversation_id.startswith("oc_"):
            customer_chat = conversation_id

        customer_facing_msg_id: str | None = None

        if kind in ("msg", "plain"):
            if customer_chat:
                mid = self.sender.send_text(customer_chat, text)
                if mid:
                    customer_facing_msg_id = mid
                    if cs_msg_id:
                        self._msg_id_map[cs_msg_id] = mid
            if thread and thread.card_msg_id:
                self.sender.reply_in_thread(thread.card_msg_id, f"[→客户] {text}")

        elif kind == "side":
            if thread and thread.card_msg_id:
                self.sender.reply_in_thread(thread.card_msg_id, f"[侧栏] {text}")

        else:
            log.debug("[outbound] unhandled kind=%s conv=%s", kind, conversation_id)

        return customer_facing_msg_id

    def on_csat_request(self, conversation_id: str) -> None:
        """向客户发送 CSAT 评分卡片。"""
        thread = self._threads.get(conversation_id)
        customer_chat = (
            thread.customer_chat_id
            if thread is not None
            else self.group_manager.get_customer_chat(conversation_id)
        )
        if customer_chat is None and conversation_id.startswith("oc_"):
            customer_chat = conversation_id
        if customer_chat:
            self.sender.send_card(customer_chat, csat_card(conversation_id))

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
        card = build_conv_card(
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
        card = build_conv_card(
            conversation_id, thread.metadata, mode=thread.mode, state="closed"
        )
        return bool(self.sender.update_card(thread.card_msg_id, card))
