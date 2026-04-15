---
type: e2e-report
id: cs-report-card-thread
status: confirmed
producer: skill-4
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-card-thread
  - cs-plan-card-thread
  - cs-diff-card-thread
evidence: []
---

# E2E Report: Task 4.6.5 — feishu_bridge card+thread 模型 + auto-hijack

## 测试范围

按测试计划 `cs-plan-card-thread` 覆盖 TC-1 ~ TC-9 自动化用例，
TC-10（完整飞书 live E2E）需真实凭证，本轮跳过。

## 用例结果

| # | 用例 | 文件 | 类型 | 结果 |
|---|------|------|------|------|
| 1 | test_conv_created_sends_card | feishu_bridge/tests/test_visibility_router.py | unit | PASS |
| 2 | test_card_is_thread_root | 同上 | unit | PASS |
| 3 | test_public_reply_dual_write | 同上 | unit | PASS |
| 4 | test_side_thread_only | 同上 | unit | PASS |
| 5 | test_mode_changed_updates_card | 同上 | unit | PASS |
| 6 | test_conv_closed_updates_card | 同上 | unit | PASS |
| 7 | test_msg_id_mapping_for_edit | 同上 | unit | PASS |
| 7b | test_edit_without_mapping_still_leaves_thread_trace | 同上 | unit | PASS |
| 8 | test_operator_in_customer_chat | feishu_bridge/tests/test_group_manager.py | unit | PASS |
| 9 | test_operator_in_customer_chat_triggers_hijack_callback | feishu_bridge/tests/test_auto_hijack.py | unit | PASS |
| 9a | test_customer_in_customer_chat_does_not_trigger | 同上 | unit | PASS |
| 9b | test_operator_in_squad_chat_does_not_trigger | 同上 | unit | PASS |
| 9c | test_auto_hijack_callback_exception_is_swallowed | 同上 | unit | PASS |
| 10 | test_card_thread_e2e | tests/e2e/test_feishu_card_thread.py | E2E（飞书凭证） | SKIP（缺凭证） |

## 回归验证

- channel-server unit suite：158 passed。
- feishu_bridge unit suite：35 passed（含 TC-018/TC-019 适配新 thread 模型）。
- 合计 193 passed / 0 failed / 4 warnings（均为 lark_oapi/websockets 弃用提示，与本任务无关）。

## 命令

```bash
uv run pytest tests/unit/ feishu_bridge/tests/ -q
# 193 passed in 119.45s
```

## 风险与跟进

- TC-10（live feishu E2E）缺真实 app_id/app_secret，建议待 staging 环境联调时单独跑。
- `bridge.py` 的 `on_auto_hijack` 钩子目前需要 app 层显式注入，下一步在
  独立进程入口（cs-bridge / app）里把它接到 Bridge API WebSocket 客户端。
