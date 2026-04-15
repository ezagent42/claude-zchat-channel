# Test-plan: 飞书卡片回调 — CardAwareClient + CSAT 评分闭环

| 字段 | 值 |
|------|-----|
| ID | cs-plan-card-action |
| 类型 | test-plan |
| 状态 | executed |
| 产出者 | skill-2 |
| 消费者 | skill-3 |
| 输入 | cs-eval-card-action |
| 创建时间 | 2026-04-15T19:05:00Z |

## 测试用例

### Unit Tests — feishu_bridge/tests/test_card_action.py

| TC-ID | 测试名 | 类型 | 优先级 | 验证点 | BEH |
|-------|--------|------|--------|--------|-----|
| TC-1 | test_card_aware_client_dispatches_card | unit | P0 | CARD 帧 → card_handler 被调用，payload 正确 | BEH-1 |
| TC-2 | test_event_frame_delegates_to_super | unit | P0 | EVENT 帧 → 走原 SDK 逻辑，card_handler 不被调用 | BEH-5 |
| TC-3 | test_card_handler_exception_swallowed | unit | P1 | handler 抛异常 → 连接不断，返回 500 Response | BEH-6 |
| TC-4 | test_card_action_extracts_score | unit | P0 | payload `{"action":{"value":{"score":"4","conv_id":"c1"}}}` → 解析出 score=4, conv_id="c1" | BEH-2 |
| TC-5 | test_card_action_sends_csat_to_bridge | unit | P0 | 解析后通过 Bridge API 发送 customer_message + csat_score | BEH-3 |
| TC-6 | test_card_action_missing_fields_noop | unit | P1 | 缺 score 或 conv_id → 不发送，不报错 | BEH-7 |

### E2E Tests — tests/e2e/test_csat_flow.py

| TC-ID | 测试名 | 类型 | 优先级 | 验证点 | BEH |
|-------|--------|------|--------|--------|-----|
| TC-7 | test_csat_e2e_card_to_score | E2E | P0 | 模拟 card action → Bridge API 收到 → conversation.resolution.csat_score 被设置 | BEH-3, BEH-4 |
| TC-8 | test_csat_e2e_invalid_score_ignored | E2E | P1 | 无效 score → conversation 不受影响 | BEH-7 |

## 测试策略

- **Unit**: mock SDK 内部（Frame、headers、_write_message），mock Bridge API WebSocket
- **E2E**: 启动真 ergo + channel-server，通过 Bridge API WebSocket 发送 customer_message（模拟 bridge 转发 CSAT）
- **回归**: 全套 `tests/unit/ tests/e2e/ feishu_bridge/tests/` 必须 PASS

## 关联 Artifact

- eval-doc: cs-eval-card-action
- code-diff: cs-diff-card-action
- e2e-report: cs-report-card-action
