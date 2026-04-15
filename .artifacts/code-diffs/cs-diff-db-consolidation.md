---
type: code-diff
id: cs-diff-db-consolidation
status: confirmed
producer: skill-3
created_at: "2026-04-15"
related:
  - cs-eval-db-consolidation
  - cs-plan-db-consolidation
  - cs-report-db-consolidation
---

# Code Diff: SQLite 数据库合并

## 新增文件

### engine/db.py (~70 行)
- `init_db(path: str) -> sqlite3.Connection` — 统一建 5 表 + 索引 + PRAGMA WAL + FK
- 目标 schema 完全匹配 spec §11

### tests/unit/test_db_consolidation.py (~170 行)
- TC-DB-001 ~ TC-DB-009: 9 个 unit tests

### tests/e2e/test_db_lifecycle.py (~100 行)
- TC-DB-010: 全生命周期 E2E

## 修改文件

### engine/conversation_manager.py
- `__init__(db_path: str)` → `__init__(conn: sqlite3.Connection)`
- 移除 `_init_db()` 和 `_db_path`
- `INSERT OR REPLACE` → `INSERT ... ON CONFLICT DO UPDATE`（避免 CASCADE 触发）

### engine/event_bus.py
- `__init__(db_path: str)` → `__init__(conn: sqlite3.Connection)`
- 移除 `_init_db()` 和 `_db_path`
- `INSERT OR REPLACE` → `INSERT ... ON CONFLICT DO UPDATE`

### engine/message_store.py
- `__init__(db_path: str)` → `__init__(conn: sqlite3.Connection)`
- 移除 `_init_db()` 和 `_db_path`
- `INSERT OR REPLACE` → `INSERT ... ON CONFLICT DO UPDATE`

### server.py
- 移除 `CS_EVENT_DB_PATH` / `CS_MESSAGE_DB_PATH` 环境变量
- `build_components()`: `conn = init_db(CS_DB_PATH)` → 传给 3 组件

### 测试 fixture 改造 (~10 文件)
- unit tests: `EventBus(path)` → `EventBus(init_db(path))`
- unit tests: 添加 `_seed_conversations()` 满足 FK 约束
- server integration tests: 移除 `CS_EVENT_DB_PATH`/`CS_MESSAGE_DB_PATH` monkeypatch/patch
- E2E conftest/test files: 移除多余 DB env vars

## 关键发现

`INSERT OR REPLACE` 在 SQLite 中等价于 DELETE + INSERT，会触发 FK CASCADE。
改为 `INSERT ... ON CONFLICT DO UPDATE`（真正的 UPSERT）解决。
