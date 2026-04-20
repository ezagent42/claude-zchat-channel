"""OutboundRouter — channel-server → 飞书出站路由（V6 精简版）。

按 irc_encoding.parse(content).kind 路由（bridge.py 调用前已解析）：
  kind=msg/plain → 本 bot 对应的 customer 飞书群 send_text
  kind=side      → 若有 squad thread（跨 bot 监管卡片），在 thread 回复
  kind=edit      → 查 msg_id 映射，update_message
  kind=sys       → bridge.py 提前过滤

V6 去 role 化：不再有 "squad chat" / "admin chat" 业务分类。bridge 自己知道
它负责哪些 chat_id，通过 ChannelMapper 查映射即可。跨 bot 的 squad 镜像作为
可选扩展（_threads 字典仍保留，但只有明确监管的 bridge 才使用）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from feishu_bridge.feishu_renderer import build_conv_card, csat_card

if TYPE_CHECKING:
    from feishu_bridge.group_manager import ChannelMapper
    from feishu_bridge.sender import FeishuSender

log = logging.getLogger("feishu-bridge.outbound")


@dataclass
class ConvThread:
    """跟踪一个 conversation 在某外部群的 card+thread 状态（可选，squad 监管用）。"""

    conversation_id: str
    supervising_chat_id: str          # 承载卡片的飞书群 chat_id（squad 群）
    card_msg_id: str | None = None
    customer_chat_id: str | None = None
    mode: str = "fast"
    state: str = "active"
    metadata: dict = field(default_factory=dict)


class OutboundRouter:
    """channel-server → 飞书出站路由（简化为 kind 分发，无 role 概念）。"""

    def __init__(
        self,
        sender: FeishuSender,
        mapper: ChannelMapper,
    ) -> None:
        self.sender = sender
        self.mapper = mapper
        self._threads: dict[str, ConvThread] = {}
        # cs_msg_id → feishu_msg_id（edit 事件查映射）
        self._msg_id_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_thread(self, conversation_id: str) -> ConvThread | None:
        return self._threads.get(conversation_id)

    def get_feishu_msg_id(self, cs_msg_id: str) -> str | None:
        return self._msg_id_map.get(cs_msg_id)

    def get_conversation_for_thread(self, supervising_chat_id: str) -> str | None:
        """由承载卡片的外部 chat_id 反查 conversation_id（可能多 conv 共用一 chat，仅返回首个 active）。"""
        for conv_id, thread in self._threads.items():
            if thread.supervising_chat_id == supervising_chat_id and thread.state == "active":
                return conv_id
        return None

    def get_conversation_for_card(self, card_msg_id: str) -> str | None:
        """由 card_msg_id（即 thread root 的飞书 message_id）精确反查 conversation_id。

        V6 监管场景下，一个 squad 飞书群可能承载多个 conv 的 card；thread reply
        带 parent_id == card_msg_id 时用此方法定位 conv。
        """
        if not card_msg_id:
            return None
        for conv_id, thread in self._threads.items():
            if thread.card_msg_id == card_msg_id and thread.state == "active":
                return conv_id
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_conversation_created(
        self,
        conversation_id: str,
        metadata: dict | None = None,
    ) -> str | None:
        """V6: 当前 bridge 只发本 bot 的 customer 消息，不跨 bot 发监管卡片。

        跨 bot 监管（squad 群看到 customer conv 卡片）留给未来 supervises 机制：
        squad bridge 订阅 customer channels + 调用此方法的扩展版本。
        """
        log.debug("[outbound] on_conversation_created: %s (V6 no cross-bot supervision yet)",
                  conversation_id)
        return None

    # ------------------------------------------------------------------
    # 消息路由
    # ------------------------------------------------------------------

    def route(
        self,
        conversation_id: str,
        *,
        kind: str,
        text: str,
        cs_msg_id: str | None = None,
    ) -> str | None:
        """按 kind 路由出站消息到本 bridge 负责的飞书群。

        - kind=msg/plain → send_text 到 mapper[conversation_id]
        - kind=side      → 若有 squad thread 则 thread 回复；否则 debug 忽略
        """
        customer_chat = self.mapper.get_feishu_chat(conversation_id)
        # V5 容错：conversation_id 本身就是 chat_id（一些 e2e 场景）
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
            else:
                log.debug("[outbound] no mapping for %s; msg/plain dropped", conversation_id)

        elif kind == "side":
            thread = self._threads.get(conversation_id)
            if thread and thread.card_msg_id:
                self.sender.reply_in_thread(thread.card_msg_id, f"[侧栏] {text}")
            else:
                log.debug("[outbound] side message for %s has no supervising thread",
                          conversation_id)

        else:
            log.debug("[outbound] unhandled kind=%s conv=%s", kind, conversation_id)

        return customer_facing_msg_id

    def on_csat_request(self, conversation_id: str) -> None:
        """向本 bot 对应的客户群发送 CSAT 评分卡。"""
        customer_chat = self.mapper.get_feishu_chat(conversation_id)
        if customer_chat is None and conversation_id.startswith("oc_"):
            customer_chat = conversation_id
        if customer_chat:
            self.sender.send_card(customer_chat, csat_card(conversation_id))

    # ------------------------------------------------------------------
    # 编辑
    # ------------------------------------------------------------------

    def on_edit(self, conversation_id: str, cs_msg_id: str, text: str) -> bool:
        """cs 侧 edit → update_message（查 msg_id 映射）。"""
        feishu_msg_id = self._msg_id_map.get(cs_msg_id)
        if not feishu_msg_id:
            return False
        return bool(self.sender.update_message(feishu_msg_id, text))

    # ------------------------------------------------------------------
    # 卡片生命周期（squad 监管可选）
    # ------------------------------------------------------------------

    def on_mode_changed(self, conversation_id: str, mode: str, **_kwargs: Any) -> bool:
        """mode 切换 → 刷新监管卡片（若有）。"""
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
