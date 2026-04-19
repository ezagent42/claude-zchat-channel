"""群 ↔ 角色映射 + 成员权限管理。

三种角色通过群成员资格授权：
- admin: 配置的 admin_chat_id 群成员
- operator: 配置的 squad_chats 群成员
- customer: bot 被拉入的动态群（持久化到 JSON）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("feishu-bridge.group_manager")


class GroupManager:
    """飞书群 chat_id → 角色映射 + 动态 customer 注册。

    V4: channel_id → feishu_chat_id 映射来自 routing.toml，
    解决 outbound 路由时从 channel_id 查到飞书群的问题。
    """

    def __init__(
        self,
        admin_chat_id: str,
        squad_chats: list[dict],
        customer_chats_path: str | None = None,
        channel_chat_map: dict[str, str] | None = None,
    ) -> None:
        self.admin_chat_id = admin_chat_id
        self.squad_chats = squad_chats
        self._customer_chats_path = customer_chats_path

        # V4: channel_id → feishu_chat_id（来自 routing.toml）
        self._channel_chat_map: dict[str, str] = dict(channel_chat_map or {})

        # 动态 customer 群集合
        self._dynamic_customer_chats: set[str] = set()
        if customer_chats_path:
            self._load_customer_chats()

        # 成员权限追踪：{chat_id: set(user_id)}
        self._admin_members: set[str] = set()
        self._squad_members: dict[str, set[str]] = {
            s["chat_id"]: set() for s in squad_chats
        }

    # ------------------------------------------------------------------
    # 角色识别
    # ------------------------------------------------------------------

    def identify_role(self, chat_id: str) -> str:
        """根据 chat_id 判断角色。"""
        if chat_id == self.admin_chat_id:
            return "admin"
        for squad in self.squad_chats:
            if chat_id == squad["chat_id"]:
                return "operator"
        if chat_id in self._dynamic_customer_chats:
            return "customer"
        return "unknown"

    def get_operator_id(self, chat_id: str) -> str | None:
        """获取 squad 群对应的 operator_id。"""
        for squad in self.squad_chats:
            if chat_id == squad["chat_id"]:
                return squad["operator_id"]
        return None

    def get_customer_chat(self, conversation_id: str) -> str | None:
        """根据 conversation_id 获取 customer 群 chat_id。

        查询顺序：
        1. routing.toml 映射（channel_id → feishu_chat_id）
        2. 动态注册的 customer 群（chat_id == conversation_id）
        """
        mapped = self._channel_chat_map.get(conversation_id)
        if mapped:
            return mapped
        if conversation_id in self._dynamic_customer_chats:
            return conversation_id
        return None

    def get_squad_chat(self, conversation_id: str) -> str | None:
        """根据 conversation_id 获取关联的 squad 群。"""
        if self.squad_chats:
            return self.squad_chats[0]["chat_id"]
        return None

    # ------------------------------------------------------------------
    # V4: routing.toml 映射更新
    # ------------------------------------------------------------------

    def set_channel_mapping(self, channel_id: str, feishu_chat_id: str) -> None:
        """动态注册 channel_id → feishu_chat_id 映射（懒创建时调用）。"""
        self._channel_chat_map[channel_id] = feishu_chat_id

    def remove_channel_mapping(self, channel_id: str) -> None:
        """移除 channel_id 映射（群解散时调用）。"""
        self._channel_chat_map.pop(channel_id, None)

    # ------------------------------------------------------------------
    # 动态 customer 注册
    # ------------------------------------------------------------------

    def register_customer_chat(self, chat_id: str) -> None:
        """bot 被拉入新群时调用。已配置的 admin/squad 群跳���。"""
        if chat_id == self.admin_chat_id:
            return
        for squad in self.squad_chats:
            if chat_id == squad["chat_id"]:
                return
        self._dynamic_customer_chats.add(chat_id)
        self._save_customer_chats()

    # ------------------------------------------------------------------
    # 成员变动
    # ------------------------------------------------------------------

    def on_member_added(self, user_id: str, chat_id: str) -> None:
        """用户加入群 → 授予对应角色权限。"""
        if chat_id == self.admin_chat_id:
            self._admin_members.add(user_id)
        elif chat_id in self._squad_members:
            self._squad_members[chat_id].add(user_id)

    def on_member_removed(self, user_id: str, chat_id: str) -> None:
        """用户退出群 → 撤销对应角色权限。"""
        if chat_id == self.admin_chat_id:
            self._admin_members.discard(user_id)
        elif chat_id in self._squad_members:
            self._squad_members[chat_id].discard(user_id)

    def on_group_disbanded(self, chat_id: str) -> None:
        """群解散 → 清理 customer 映射。"""
        self._dynamic_customer_chats.discard(chat_id)
        self._save_customer_chats()

    # ------------------------------------------------------------------
    # 权限查询
    # ------------------------------------------------------------------

    def has_admin_permission(self, user_id: str) -> bool:
        return user_id in self._admin_members

    def has_operator_permission(self, user_id: str, chat_id: str) -> bool:
        return user_id in self._squad_members.get(chat_id, set())

    def is_operator(self, user_id: str) -> bool:
        """判断 user_id 是否为任一 squad 群的已知 operator。"""
        return any(user_id in members for members in self._squad_members.values())

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load_customer_chats(self) -> None:
        if not self._customer_chats_path:
            return
        path = Path(self._customer_chats_path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._dynamic_customer_chats = set(data)
            except Exception as e:
                log.warning("Failed to load customer_chats: %s", e)

    def _save_customer_chats(self) -> None:
        if not self._customer_chats_path:
            return
        path = Path(self._customer_chats_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(sorted(self._dynamic_customer_chats), ensure_ascii=False),
            encoding="utf-8",
        )
