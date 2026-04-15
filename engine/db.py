"""统一数据库初始化 — 5 张表 + FK + CASCADE (spec §11)"""

from __future__ import annotations

import sqlite3


def init_db(path: str) -> sqlite3.Connection:
    """创建/打开数据库，建 5 张表，启用 WAL + FK。返回共享连接。"""
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    # executescript 隐式 COMMIT 后重新确保 FK 开启
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'created',
    mode TEXT NOT NULL DEFAULT 'auto',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS participants (
    conversation_id TEXT NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    participant_id TEXT NOT NULL,
    role TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (conversation_id, participant_id)
);

CREATE TABLE IF NOT EXISTS resolutions (
    conversation_id TEXT PRIMARY KEY
        REFERENCES conversations(id) ON DELETE CASCADE,
    outcome TEXT NOT NULL,
    resolved_by TEXT NOT NULL,
    csat_score INTEGER,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    conversation_id TEXT
        REFERENCES conversations(id) ON DELETE SET NULL,
    data TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_conv ON events(conversation_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type, timestamp);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public',
    timestamp TEXT NOT NULL,
    edit_of TEXT
        REFERENCES messages(id) ON DELETE SET NULL,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, timestamp);
"""
