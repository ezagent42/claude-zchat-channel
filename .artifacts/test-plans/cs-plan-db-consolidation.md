---
type: test-plan
id: cs-plan-db-consolidation
status: confirmed
producer: skill-2
created_at: "2026-04-15"
source: "cs-eval-db-consolidation + spec/11-db-consolidation.md"
related:
  - cs-eval-db-consolidation
  - cs-diff-db-consolidation
  - cs-report-db-consolidation
---

# Test Plan: SQLite 数据库合并

## 来源
- eval-doc: cs-eval-db-consolidation
- spec: docs/discuss/spec/channel-server/11-db-consolidation.md
- plan: docs/discuss/plan/08-db-consolidation.md

## 测试矩阵

| TC-ID | 测试名 | 类型 | 验证点 | 来源 | 优先级 |
|-------|--------|------|--------|------|--------|
| TC-DB-001 | test_init_db_creates_all_tables | unit | init_db() 建 5 张表 (conversations, participants, resolutions, events, messages) | eval #1 | P0 |
| TC-DB-002 | test_foreign_keys_enabled | unit | PRAGMA foreign_keys 返回 1 | eval #2 | P0 |
| TC-DB-003 | test_cascade_delete_participants | unit | 删对话 → participants 自动删除 | eval #3 | P0 |
| TC-DB-004 | test_cascade_delete_resolutions | unit | 删对话 → resolutions 自动删除 | eval #4 | P0 |
| TC-DB-005 | test_cascade_delete_messages | unit | 删对话 → messages 自动删除 | eval #5 | P0 |
| TC-DB-006 | test_events_set_null_on_delete | unit | 删对话 → events.conversation_id = NULL | eval #6 | P0 |
| TC-DB-007 | test_edit_of_set_null | unit | 删原消息 → edit_of = NULL | eval #7 | P0 |
| TC-DB-008 | test_shared_connection | unit | 3 组件共享连接，互相可见写入 | eval #8 | P0 |
| TC-DB-009 | test_fk_rejects_invalid_conv_id | unit | FK 阻止插入不存在的 conversation_id | eval #9 | P1 |
| TC-DB-010 | test_full_lifecycle_single_db | E2E | create → message → resolve → close 全链路 | eval #10 | P0 |
| TC-DB-011 | test_240_regression | regression | 全量回归 PASS | eval #11 | P0 |

## 文件分布

| 文件 | TC-IDs | 说明 |
|------|--------|------|
| tests/unit/test_db_consolidation.py | TC-DB-001 ~ TC-DB-009 | 9 个 unit tests |
| tests/e2e/test_db_lifecycle.py | TC-DB-010 | 1 个 E2E test |
| (回归) | TC-DB-011 | uv run pytest tests/ feishu_bridge/tests/ -v |

## 依赖

- engine/db.py (新建) — init_db() 函数
- engine/conversation_manager.py — 改为接收 conn
- engine/event_bus.py — 改为接收 conn
- engine/message_store.py — 改为接收 conn
