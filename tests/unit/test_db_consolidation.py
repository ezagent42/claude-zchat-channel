"""DB 合并 unit tests — init_db + FK + CASCADE (spec §11)

TC-DB-001 ~ TC-DB-009: 验证 engine/db.py 的 init_db() 创建的统一数据库
包含 5 张表、启用 FK 约束、CASCADE/SET NULL 策略正确。
"""

from __future__ import annotations

import sqlite3

import pytest

from engine.db import init_db


@pytest.fixture
def conn(tmp_path):
    """通过 init_db 创建测试用数据库连接。"""
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()


# ------------------------------------------------------------------ #
# TC-DB-001: init_db 创建全部 5 表
# ------------------------------------------------------------------ #


def test_init_db_creates_all_tables(conn):
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert tables == {"conversations", "participants", "resolutions", "events", "messages"}


# ------------------------------------------------------------------ #
# TC-DB-002: PRAGMA foreign_keys 生效
# ------------------------------------------------------------------ #


def test_foreign_keys_enabled(conn):
    result = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert result == 1


# ------------------------------------------------------------------ #
# TC-DB-003: CASCADE 删除 participants
# ------------------------------------------------------------------ #


def test_cascade_delete_participants(conn):
    conn.execute(
        "INSERT INTO conversations (id, state, mode, created_at, updated_at) "
        "VALUES ('c1', 'active', 'auto', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO participants (conversation_id, participant_id, role, joined_at) "
        "VALUES ('c1', 'alice', 'operator', '2026-01-01')"
    )
    conn.commit()

    conn.execute("DELETE FROM conversations WHERE id = 'c1'")
    conn.commit()

    rows = conn.execute("SELECT * FROM participants WHERE conversation_id = 'c1'").fetchall()
    assert len(rows) == 0


# ------------------------------------------------------------------ #
# TC-DB-004: CASCADE 删除 resolutions
# ------------------------------------------------------------------ #


def test_cascade_delete_resolutions(conn):
    conn.execute(
        "INSERT INTO conversations (id, state, mode, created_at, updated_at) "
        "VALUES ('c1', 'active', 'auto', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO resolutions (conversation_id, outcome, resolved_by, timestamp) "
        "VALUES ('c1', 'resolved', 'alice', '2026-01-01')"
    )
    conn.commit()

    conn.execute("DELETE FROM conversations WHERE id = 'c1'")
    conn.commit()

    rows = conn.execute("SELECT * FROM resolutions WHERE conversation_id = 'c1'").fetchall()
    assert len(rows) == 0


# ------------------------------------------------------------------ #
# TC-DB-005: CASCADE 删除 messages
# ------------------------------------------------------------------ #


def test_cascade_delete_messages(conn):
    conn.execute(
        "INSERT INTO conversations (id, state, mode, created_at, updated_at) "
        "VALUES ('c1', 'active', 'auto', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO messages (id, conversation_id, source, content, visibility, timestamp) "
        "VALUES ('m1', 'c1', 'agent0', 'hello', 'public', '2026-01-01')"
    )
    conn.commit()

    conn.execute("DELETE FROM conversations WHERE id = 'c1'")
    conn.commit()

    rows = conn.execute("SELECT * FROM messages WHERE conversation_id = 'c1'").fetchall()
    assert len(rows) == 0


# ------------------------------------------------------------------ #
# TC-DB-006: events SET NULL on delete
# ------------------------------------------------------------------ #


def test_events_set_null_on_delete(conn):
    conn.execute(
        "INSERT INTO conversations (id, state, mode, created_at, updated_at) "
        "VALUES ('c1', 'active', 'auto', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO events (id, type, conversation_id, data, timestamp) "
        "VALUES ('e1', 'conversation.created', 'c1', '{}', '2026-01-01')"
    )
    conn.commit()

    conn.execute("DELETE FROM conversations WHERE id = 'c1'")
    conn.commit()

    row = conn.execute("SELECT conversation_id FROM events WHERE id = 'e1'").fetchone()
    assert row is not None, "event 应保留（审计日志）"
    assert row[0] is None, "conversation_id 应变为 NULL"


# ------------------------------------------------------------------ #
# TC-DB-007: edit_of SET NULL
# ------------------------------------------------------------------ #


def test_edit_of_set_null(conn):
    conn.execute(
        "INSERT INTO conversations (id, state, mode, created_at, updated_at) "
        "VALUES ('c1', 'active', 'auto', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO messages (id, conversation_id, source, content, visibility, timestamp) "
        "VALUES ('m1', 'c1', 'agent0', 'original', 'public', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO messages (id, conversation_id, source, content, visibility, timestamp, edit_of) "
        "VALUES ('m2', 'c1', 'agent0', 'revised', 'public', '2026-01-01', 'm1')"
    )
    conn.commit()

    # 删除原消息 m1 — 编辑版本 m2 的 edit_of 应变 NULL
    conn.execute("DELETE FROM messages WHERE id = 'm1'")
    conn.commit()

    row = conn.execute("SELECT edit_of FROM messages WHERE id = 'm2'").fetchone()
    assert row is not None, "编辑版本应保留"
    assert row[0] is None, "edit_of 应变为 NULL"


# ------------------------------------------------------------------ #
# TC-DB-008: 3 组件共享连接
# ------------------------------------------------------------------ #


def test_shared_connection(conn):
    from engine.conversation_manager import ConversationManager
    from engine.event_bus import EventBus
    from engine.message_store import MessageStore

    cm = ConversationManager(conn)
    eb = EventBus(conn)
    ms = MessageStore(conn)

    # ConversationManager 写入
    cm.create("c1")
    cm.activate("c1")

    # EventBus 通过同一连接可见
    import asyncio
    from protocol.event import Event, EventType

    asyncio.run(eb.publish(Event(type=EventType.CONVERSATION_CREATED, conversation_id="c1")))

    # MessageStore 通过同一连接可见
    from protocol.message_types import Message, MessageVisibility

    ms.save(
        Message(
            id="m1",
            source="agent0",
            conversation_id="c1",
            content="hello",
            visibility=MessageVisibility.PUBLIC,
        )
    )

    # 验证所有写入互相可见
    events = eb.query(conversation_id="c1")
    assert len(events) == 1
    msgs = ms.query_by_conversation("c1")
    assert len(msgs) == 1
    conv = cm.get("c1")
    assert conv is not None


# ------------------------------------------------------------------ #
# TC-DB-009: FK 阻止无效 conversation_id
# ------------------------------------------------------------------ #


def test_fk_rejects_invalid_conv_id(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO participants (conversation_id, participant_id, role, joined_at) "
            "VALUES ('nonexistent', 'alice', 'operator', '2026-01-01')"
        )
