"""MessageStore unit tests — 消息持久化 + 编辑 + 查询"""

from __future__ import annotations

import pytest

from engine.message_store import MessageStore
from zchat_protocol.message_types import Message, MessageVisibility


def make_msg(mid: str, conv_id: str, text: str = "hi") -> Message:
    return Message(
        id=mid,
        source="agent0",
        conversation_id=conv_id,
        content=text,
        visibility=MessageVisibility.PUBLIC,
    )


def _seed_conversations(conn, *conv_ids):
    """预插入 conversation 行以满足 FK 约束。"""
    for cid in conv_ids:
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, state, mode, created_at, updated_at) "
            "VALUES (?, 'active', 'auto', '2026-01-01', '2026-01-01')",
            (cid,),
        )
    conn.commit()


@pytest.fixture
def store(tmp_path):
    from engine.db import init_db

    conn = init_db(str(tmp_path / "msg.db"))
    _seed_conversations(conn, "c1", "c2")
    return MessageStore(conn)


def test_save_and_get(store):
    m = make_msg("m1", "c1", "hello")
    store.save(m)
    loaded = store.get("m1")
    assert loaded is not None
    assert loaded.content == "hello"
    assert loaded.visibility == MessageVisibility.PUBLIC


def test_get_unknown(store):
    assert store.get("missing") is None


def test_edit(store):
    store.save(make_msg("m1", "c1", "original"))
    edited = store.edit("m1", "revised")
    assert edited.content == "revised"
    assert edited.edit_of == "m1"
    # 原消息仍可读取
    assert store.get("m1").content == "original"


def test_edit_unknown_raises(store):
    with pytest.raises(KeyError):
        store.edit("missing", "x")


def test_query_by_conversation(store):
    store.save(make_msg("m1", "c1", "a"))
    store.save(make_msg("m2", "c1", "b"))
    store.save(make_msg("m3", "c2", "other"))
    results = store.query_by_conversation("c1")
    assert len(results) == 2
    assert {m.content for m in results} == {"a", "b"}


def test_query_preserves_order(store):
    import time

    for i, text in enumerate(["first", "second", "third"]):
        store.save(make_msg(f"m{i}", "c1", text))
        time.sleep(0.001)
    results = store.query_by_conversation("c1")
    assert [m.content for m in results] == ["first", "second", "third"]


def test_persistence_across_instances(tmp_path):
    from engine.db import init_db

    db = str(tmp_path / "msg.db")
    conn1 = init_db(db)
    _seed_conversations(conn1, "c1")
    s1 = MessageStore(conn1)
    s1.save(make_msg("m1", "c1", "hello"))
    conn1.close()
    conn2 = init_db(db)
    s2 = MessageStore(conn2)
    assert s2.get("m1").content == "hello"
