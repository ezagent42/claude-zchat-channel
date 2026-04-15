---
type: e2e-report
id: cs-report-db-consolidation
status: confirmed
producer: skill-4
created_at: "2026-04-15"
related:
  - cs-eval-db-consolidation
  - cs-plan-db-consolidation
  - cs-diff-db-consolidation
---

# Test Report: SQLite 数据库合并

## 测试结果

| 类别 | 数量 | 通过 | 失败 | 错误 |
|------|------|------|------|------|
| 新增 unit | 9 | 9 | 0 | 0 |
| 新增 E2E | 1 | 1 | 0 | 0 |
| 回归 (unit + feishu) | 185 + 53 = 238 | 238 | 0 | 0 |
| 回归 (E2E) | 10 | 8 | 0 | 2 (网络超时) |
| **总计** | **250** | **248** | **0** | **2** |

## 新增测试通过详情

| TC-ID | 测试名 | 结果 |
|-------|--------|------|
| TC-DB-001 | test_init_db_creates_all_tables | PASSED |
| TC-DB-002 | test_foreign_keys_enabled | PASSED |
| TC-DB-003 | test_cascade_delete_participants | PASSED |
| TC-DB-004 | test_cascade_delete_resolutions | PASSED |
| TC-DB-005 | test_cascade_delete_messages | PASSED |
| TC-DB-006 | test_events_set_null_on_delete | PASSED |
| TC-DB-007 | test_edit_of_set_null | PASSED |
| TC-DB-008 | test_shared_connection | PASSED |
| TC-DB-009 | test_fk_rejects_invalid_conv_id | PASSED |
| TC-DB-010 | test_full_lifecycle_single_db | PASSED |

## E2E Errors (非回归)

2 个 E2E error 均为网络连接超时（ergo server 启动慢），与 DB 合并无关：
- test_command_handlers.py::test_status_returns_formatted_reply
- test_routing.py::test_dispatch_whitelist_pass_e2e

基线同样有 3 个类似 E2E error。

## 基线对比

| 指标 | 基线 | 合并后 | 差异 |
|------|------|--------|------|
| passed | 237 | 248 | +11 |
| failed | 0 | 0 | 0 |
| errors | 3 | 2 | -1 |
