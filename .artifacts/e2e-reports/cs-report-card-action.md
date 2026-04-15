# E2E Report: 飞书卡片回调 — CardAwareClient + CSAT 评分闭环

| 字段 | 值 |
|------|-----|
| ID | cs-report-card-action |
| 类型 | e2e-report |
| 状态 | confirmed |
| 产出者 | skill-4 |
| 创建时间 | 2026-04-15T19:30:00Z |

## 测试结果

### 基线

- 修改前: 232 passed, 0 failed

### 新增测试

| TC-ID | 测试名 | 结果 |
|-------|--------|------|
| TC-1 | test_card_aware_client_dispatches_card | PASSED |
| TC-2 | test_event_frame_delegates_to_super | PASSED |
| TC-3 | test_card_handler_exception_swallowed | PASSED |
| TC-4 | test_card_action_extracts_score | PASSED |
| TC-5 | test_card_action_sends_csat_to_bridge | PASSED |
| TC-6 | test_card_action_missing_fields_noop | PASSED |
| TC-7 | test_csat_e2e_card_to_score | PASSED |
| TC-8 | test_csat_e2e_invalid_score_ignored | PASSED |

### 回归

- 修改后: **240 passed, 0 failed** (232 基线 + 8 新增)
- 回归: **0 FAIL**

## 关联 Artifact

- eval-doc: cs-eval-card-action
- test-plan: cs-plan-card-action
- code-diff: cs-diff-card-action
