"""E2E: customer_connect 创建 conversation。"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def test_customer_connect_creates_conversation(bridge_ws, channel_server, tmp_path):
    """发 customer_connect 后，直接读 SQLite 验证 conversation 行存在。"""
    conv_id = f"e2e_cc_{os.getpid()}"
    await bridge_ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "david", "name": "David"},
                "metadata": {"source": "e2e"},
            }
        )
    )

    # 让服务端处理
    await asyncio.sleep(1.0)

    # channel_server fixture 在 tmp_path 下写 conv.db
    # fixture scope 是 function，所以 tmp_path 是本次测试的
    db_path = tmp_path / "conv.db"

    # 等待 DB 文件出现（最多 5s）
    for _ in range(50):
        if db_path.exists():
            break
        await asyncio.sleep(0.1)

    assert db_path.exists(), f"conversation db not created at {db_path}"

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id, metadata FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, f"conversation {conv_id} not found in DB"
    assert row[0] == conv_id
    meta = json.loads(row[1]) if row[1] else {}
    assert meta.get("customer", {}).get("id") == "david"
