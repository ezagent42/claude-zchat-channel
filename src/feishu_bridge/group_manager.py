"""飞书 chat_id ↔ CS channel_id 映射（V6 精简版）。

V5 的 `identify_role` / `admin_chat_id` / `squad_chats` 在 V6 一 bot 一 bridge
架构下是冗余业务分类 —— bridge 本身就是"一个 bot 一个角色"，role 由 bot_name
决定。消息级别的 customer-visible vs operator-visible 用 `__side:` / `__msg:`
前缀（spec §5），不在这里分类。

本模块现仅保留双向映射查询：
  - channel_id → feishu chat_id（出站：IRC channel 发回飞书时用）
  - feishu chat_id → channel_id（入站：bridge 查 external → channel 映射，
    此方向在 routing_reader 统一管，本模块只暴露反查）
"""

from __future__ import annotations

import logging

log = logging.getLogger("feishu-bridge.group_manager")


class ChannelMapper:
    """channel_id ↔ feishu chat_id 的简单双向映射。

    channel_id 总是以 routing.toml 裸名形式存储（lstrip '#'）。
    """

    def __init__(self, channel_chat_map: dict[str, str] | None = None) -> None:
        # channel_id → feishu_chat_id
        self._channel_chat_map: dict[str, str] = {
            (k or "").lstrip("#"): v for k, v in (channel_chat_map or {}).items()
        }

    def get_feishu_chat(self, channel_id: str) -> str | None:
        """由 CS channel_id 查飞书 chat_id。"""
        return self._channel_chat_map.get((channel_id or "").lstrip("#"))

    def replace_all(self, channel_chat_map: dict[str, str]) -> None:
        """批量替换（routing.toml reload 时调用）。"""
        self._channel_chat_map = {
            (k or "").lstrip("#"): v for k, v in channel_chat_map.items()
        }
