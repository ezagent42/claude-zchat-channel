---
type: test-plan
id: cs-plan-card-thread
status: executed
producer: skill-2
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-card-thread
  - cs-diff-card-thread
  - cs-report-card-thread
---

# Test Plan: Task 4.6.5 — feishu_bridge card+thread 模型 + auto-hijack

## 来源

- eval-doc: `cs-eval-card-thread`
- plan: `docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.5
- code 改动范围：`feishu_bridge/visibility_router.py`（重写）+ `feishu_bridge/sender.py`（新增 reply/update API）+ `feishu_bridge/group_manager.py`（+is_operator_in_customer_chat）+ `feishu_bridge/bridge.py`（auto-hijack 检测）

## 用例列表

### VisibilityRouter unit tests (`feishu_bridge/tests/test_visibility_router.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-1 | test_conv_created_sends_card | P0 | eval #1 | `on_conversation_created` 调用 `sender.send_card`，返回 card_msg_id |
| TC-2 | test_card_is_thread_root | P0 | eval #2 | card 包含 header (conv_id + 状态) + elements (fields + 操作按钮) |
| TC-3 | test_public_reply_dual_write | P0 | eval #3 | public → `send_text(customer)` + `reply_in_thread(card_msg_id)` |
| TC-4 | test_side_thread_only | P0 | eval #4 | side → 仅 `reply_in_thread`，不调用 `send_text` |
| TC-5 | test_mode_changed_updates_card | P1 | eval #8 | `on_mode_changed` → `update_card` 刷新 mode 标签 |
| TC-6 | test_conv_closed_updates_card | P1 | eval #9 | `on_conversation_closed` → `update_card` 标记 closed + 注入 resolution |
| TC-7 | test_msg_id_mapping_for_edit | P0 | eval #6,#7 | public 消息建立 msg_id 映射 → `on_edit` 通过映射执行 `update_message` |
| TC-7b | test_edit_without_mapping_still_leaves_thread_trace | P1 | eval #7 边界 | edit 无映射时仍在 thread 追加 `[edited]` 标记 |

### GroupManager unit tests (`feishu_bridge/tests/test_group_manager.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-8 | test_operator_in_customer_chat | P0 | eval #10 | `is_operator_in_customer_chat` 对动态 customer 群 + squad operator → True |

### Auto-hijack unit tests (`feishu_bridge/tests/test_auto_hijack.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-9 | test_operator_in_customer_chat_triggers_hijack_callback | P0 | eval #11 | mock P2ImMessageReceiveV1 事件 → `on_auto_hijack` 被调用 |
| TC-9a | test_customer_in_customer_chat_does_not_trigger | P0 | eval #11 反例 | 客户自己发言不触发 hijack |
| TC-9b | test_operator_in_squad_chat_does_not_trigger | P1 | eval #11 反例 | operator 在 squad 群发言不触发 hijack |
| TC-9c | test_auto_hijack_callback_exception_is_swallowed | P1 | eval #12 | 回调抛异常 → 被吞掉，不影响主流程 |

### Visibility 适配 (`feishu_bridge/tests/test_visibility.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-018 | test_visibility_public_uses_thread | P0 | eval #3 适配 | 已有 visibility 测试适配新 thread 模型 |
| TC-019 | test_visibility_side_no_customer | P0 | eval #4 适配 | side 不到达 customer_chat |

### E2E (`tests/e2e/test_feishu_card_thread.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-10 | test_card_thread_e2e | P2 | eval 全链路 | 需真实飞书凭证，完整 card 创建 → thread 回复 → edit → close |

## 统计

- 总数：15 unit + 1 E2E (TC-10 条件跳过)
- P0: 9 (TC-1, TC-2, TC-3, TC-4, TC-7, TC-8, TC-9, TC-9a, TC-018, TC-019)
- P1: 4 (TC-5, TC-6, TC-7b, TC-9b, TC-9c)
- P2: 1 (TC-10)

## 验证策略

1. **Mock sender**：所有 unit 测试通过 mock `FeishuSender` 验证调用序列和参数，不依赖飞书 API。
2. **ConvThread 状态检查**：验证 `_threads` / `_msg_id_map` 内部状态在各操作后的正确性。
3. **Mock P2ImMessageReceiveV1**：auto-hijack 测试通过 mock 飞书事件对象验证 detection 逻辑。
4. **回归确认**：全套 `uv run pytest tests/unit/ feishu_bridge/tests/ -q` → 193 passed / 0 failed。

## 风险

- TC-10 需真实 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，建议在 staging 环境单独运行。
- `reply_in_thread_sync` 依赖 lark-oapi `ReplyMessageRequest`，若 SDK 版本升级 API 变更需适配。

## 实现要点

1. test_visibility_router.py 使用 `unittest.mock.MagicMock` 替代 sender / group_manager。
2. test_auto_hijack.py 构造最小化 `P2ImMessageReceiveV1` 事件，验证 bridge 内部 detection 链路。
3. 所有测试无外部依赖（IRC / 飞书 / 数据库），纯 mock 驱动。
