"""E2E: DB 合并全生命周期测试 (TC-DB-010)

create → activate → add_participant → message → resolve → close
验证单一数据库内所有表数据一致。
"""

from __future__ import annotations

import asyncio

import pytest

from engine.conversation_manager import ConversationManager
from engine.db import init_db
from engine.event_bus import EventBus
from engine.message_store import MessageStore
from protocol.conversation import ConversationState
from protocol.event import Event, EventType
from protocol.message_types import Message, MessageVisibility
from protocol.participant import Participant, ParticipantRole


@pytest.fixture
def components(tmp_path):
    """组装 3 组件共享同一数据库连接。"""
    conn = init_db(str(tmp_path / "lifecycle.db"))
    cm = ConversationManager(conn)
    eb = EventBus(conn)
    ms = MessageStore(conn)
    yield {"conn": conn, "cm": cm, "eb": eb, "ms": ms}
    conn.close()


def test_full_lifecycle_single_db(components):
    """TC-DB-010: create → message → resolve → close 全链路验证。"""
    conn = components["conn"]
    cm = components["cm"]
    eb = components["eb"]
    ms = components["ms"]

    # 1. 创建对话
    conv = cm.create("conv-lifecycle")
    assert conv.id == "conv-lifecycle"

    # 2. 激活
    cm.activate("conv-lifecycle")

    # 3. 添加参与者
    op = Participant(id="xiaoli", role=ParticipantRole.OPERATOR)
    cm.add_participant("conv-lifecycle", op)

    # 4. 发送消息
    msg = Message(
        id="msg-001",
        source="customer",
        conversation_id="conv-lifecycle",
        content="请帮我处理一下",
        visibility=MessageVisibility.PUBLIC,
    )
    ms.save(msg)

    # 5. 发布事件
    asyncio.run(
        eb.publish(
            Event(
                type=EventType.MESSAGE_SENT,
                conversation_id="conv-lifecycle",
                data={"message_id": "msg-001"},
            )
        )
    )

    # 6. 解决对话
    cm.resolve("conv-lifecycle", outcome="resolved", resolved_by="xiaoli")

    # ---- 验证 ----

    # 对话已关闭
    conv = cm.get("conv-lifecycle")
    assert conv.state == ConversationState.CLOSED
    assert conv.resolution is not None
    assert conv.resolution.resolved_by == "xiaoli"

    # 消息存在
    msgs = ms.query_by_conversation("conv-lifecycle")
    assert len(msgs) == 1
    assert msgs[0].content == "请帮我处理一下"

    # 事件存在
    events = eb.query(conversation_id="conv-lifecycle")
    assert len(events) >= 1

    # 参与者存在
    parts = conn.execute(
        "SELECT participant_id FROM participants WHERE conversation_id = ?",
        ("conv-lifecycle",),
    ).fetchall()
    assert any(p[0] == "xiaoli" for p in parts)

    # Resolution 存在
    res = conn.execute(
        "SELECT outcome FROM resolutions WHERE conversation_id = ?",
        ("conv-lifecycle",),
    ).fetchone()
    assert res[0] == "resolved"

    # 7. 验证 FK — 单库内全部一致
    fk_check = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk_check == 1

    # 8. 文件数验证 — 只有 1 个 db 文件
    import os
    db_dir = os.path.dirname(str(conn.execute("PRAGMA database_list").fetchone()[2]))
    db_files = [f for f in os.listdir(db_dir) if f.endswith(".db")]
    assert len(db_files) == 1, f"应只有 1 个 db 文件，实际: {db_files}"
