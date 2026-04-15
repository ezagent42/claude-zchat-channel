---
type: test-plan
id: cs-plan-review-sla
status: executed
producer: skill-2
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-review-sla
  - cs-diff-review-sla
---

# Test Plan: Task 4.6.4 — /review 命令 + SLA breach 告警

## 来源

- eval-doc: `cs-eval-review-sla`
- plan: `docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.4
- code 改动范围：`protocol/commands.py`（review 命令定义）+ `server.py`（/review handler :262-303 + _on_sla_breach :490-511）

## 用例列表

### Unit tests (`tests/unit/test_review_command.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-001 | test_review_command_parse | P0 | eval #1 | parse_command("/review") → name=review, args={} |
| TC-002 | test_review_returns_stats | P0 | eval #2 | 有对话+事件时 /review 返回包含 4 项统计的格式化文本 |
| TC-003 | test_review_empty_data | P0 | eval #3 | 无对话时 /review 返回 "暂无统计数据" |
| TC-004 | test_sla_breach_alert_format | P0 | eval #5,#6 | SLA timer 超时 → send_event("sla.breach") 含 conv_id/breach_type/timeout_seconds + send_reply 告警文本 |
| TC-005 | test_non_sla_timer_no_alert | P0 | eval #7 | 非 SLA timer（idle_timeout）→ 不触发 sla.breach event |

### E2E tests (`tests/e2e/test_sla_alerts.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-S01 | test_review_command_e2e | P0 | eval #4 | /review → Bridge 收到 system visibility reply 含 [review] 前缀 |
| TC-S02 | test_review_with_conversation | P0 | eval #2 | 创建对话后 /review → 统计包含 "对话数:" |

## 统计

- 总数：5 unit + 2 E2E = 7
- P0: 7
- P1: 0

## 验证策略

1. **命令解析**：直接调用 `parse_command("/review")` 验证返回的 `Command` 对象。
2. **Handler 集成**：通过 `build_components()` + `wire_bridge_callbacks()` 组装完整 engine（使用 `:memory:` SQLite），创建对话和事件后调用 `bridge.on_admin_command`，断言 `send_reply` 参数。
3. **SLA 告警**：通过 `event_bus.publish(Event(TIMER_EXPIRED, ...))` 模拟 timer 超时，验证 `send_event` 和 `send_reply` 调用。
4. **E2E**：通过 Bridge WebSocket 发送 `admin_command /review`，验证返回的 reply message。

## 风险

- `test_review_returns_stats` 需要组装完整 engine 并创建对话 + 发布事件，测试较重（但使用内存 DB 避免磁盘 I/O）。
- SLA 告警测试依赖 EventBus 订阅机制正确工作。

## 实现要点

1. 所有 unit 测试使用 `patch("server.CS_DB_PATH", ":memory:")` 等 mock 避免外部依赖。
2. `test_review_returns_stats` 需要完整生命周期：`create → activate → resolve → set_csat`，以验证 CSAT 统计。
3. `test_sla_breach_alert_format` 直接 publish `TIMER_EXPIRED` event，验证 `_on_sla_breach` handler 的 send_event 和 send_reply 调用。
4. 每个测试结束后调用 `event_bus.close()` / `conversation_manager.close_db()` / `message_store.close()` 清理资源。
