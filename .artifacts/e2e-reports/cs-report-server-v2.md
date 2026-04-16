---
id: cs-report-server-v2
type: e2e-report
status: pass
phase: "Phase 4 补全: mode switching + gate enforcement"
created_at: "2026-04-15"
related_ids:
  - cs-plan-server-v2
  - cs-eval-server-v2
  - cs-diff-server-v2
  - cs-test-diff-server-v2
---

# cs-report-server-v2 — Phase 4 补全 E2E 测试报告

## 概览

| 指标 | 值 |
|------|----|
| 总测试数 | 125 |
| 通过 | 125 |
| 失败 | 0 |
| 跳过 | 0 |
| 错误 | 0 |
| 执行时长 | 84.47s（全套）/ 18.62s（unit）/ 33.99s（e2e） |
| 回归失败 | **0**（4 条原有 E2E 全部继续 PASS） |

## 结果分类

### 新增用例（8 条 — 来自 cs-test-diff-server-v2）

| TC-ID | 测试函数 | 状态 |
|-------|---------|------|
| TC-U12 | `test_bridge_api.py::test_on_operator_join_callback_invoked` | PASS |
| TC-U13 | `test_bridge_api.py::test_send_event_broadcasts_to_all_connections` | PASS |
| TC-U14 | `test_bridge_api.py::test_send_event_no_connections_noop` | PASS |
| TC-U15 | `test_server_integration.py::test_build_components_injects_operator_callbacks` | PASS |
| TC-E05 | `test_mode_switching.py::test_operator_join_triggers_copilot` | PASS |
| TC-E06 | `test_mode_switching.py::test_hijack_triggers_takeover` | PASS |
| TC-E07 | `test_gate_enforcement.py::test_side_message_not_received_by_customer` | PASS |
| TC-E08 | `test_gate_enforcement.py::test_mode_changed_event_broadcast_to_all` | PASS |

### 回归用例（原有 117 条）

| 来源 | 数量 | 状态 |
|------|------|------|
| 原有 102 条 unit（Phase 1–3） | 102 | 全 PASS |
| Phase 4 新增 unit（test_irc_transport + test_server_integration 原有 4 条） | 11 | 全 PASS |
| 原有 4 条 E2E（registration / customer_connect / startup） | 4 | 全 PASS |

**无回归失败。**

## E2E 层分层结果（8 PASS）

```
uv run pytest tests/e2e/ -v -m e2e --timeout=30
============================== 8 passed in 33.99s
```

| 测试 | 类型 | 结果 |
|------|------|------|
| `test_bridge_registration.py::test_register_returns_registered_ack` | 回归 | PASS |
| `test_bridge_registration.py::test_register_capabilities_preserved` | 回归 | PASS |
| `test_customer_connect.py::test_customer_connect_creates_conversation` | 回归 | PASS |
| `test_gate_enforcement.py::test_side_message_not_received_by_customer` | **新增 TC-E07** | PASS |
| `test_gate_enforcement.py::test_mode_changed_event_broadcast_to_all` | **新增 TC-E08** | PASS |
| `test_mode_switching.py::test_operator_join_triggers_copilot` | **新增 TC-E05** | PASS |
| `test_mode_switching.py::test_hijack_triggers_takeover` | **新增 TC-E06** | PASS |
| `test_server_startup.py::test_server_subprocess_alive` | 回归 | PASS |

## 调试记录（TC-E07 首次运行失败分析）

**问题**：首次运行 TC-E07 时，`customer_ws.recv()` 未超时失败。

**根因**：`_setup_takeover()` 通过 `operator_ws` 驱动 auto→copilot→takeover，期间
`BridgeAPIServer.send_event()` 广播 2 个 `mode.changed` 事件到**所有**连接包括
`customer_ws`。这些事件队列在 `customer_ws` buffer 中，导致断言"customer_ws
不收到任何消息"失败——实际上它收到了 mode.changed（合理行为，因为 mode.changed
是协议级广播，非 visibility 过滤对象）。

**修复**：在断言 customer_ws 静默前，先 drain 掉积压的 2 个 mode.changed 广播事件，
然后验证 side visibility 的 reply 确实未到达 customer。这正确区分了"mode.changed
是协议广播（客户可见）"和"side reply 是 visibility 过滤（客户不可见）"两类行为。

## 覆盖的关键新增实现

1. `bridge_api/ws_server.py`：新增 `on_operator_join` 回调槽 + handler；新增
   `send_event()` 广播方法（无 visibility 过滤，用于协议级事件）。
2. `server.py`：新增 `wire_bridge_callbacks(bridge_server, components)` 函数，
   注入 `on_operator_join`（add_participant + ModeManager.atransition）和
   `on_operator_command`（/hijack → takeover + side 系统通知）。
3. `main()` 中在 Bridge 启动前调用 `wire_bridge_callbacks()`。

## E2E 栈

- `ergo 2.18.0`（session scope，监听 17xxx 端口段）
- `channel-server` 子进程（`uv run python -m server`，stdin 挂起）
- 多个 Bridge WebSocket 连接（单角色 / 全角色 fixture）

## 结论

Phase 4 补全完成：125 passed / 0 failed / 0 skipped。
mode switching（operator_join → copilot；/hijack → takeover）和 gate enforcement
（side visibility 不到 customer bridge；mode.changed 广播到所有 bridge）E2E 验证
全部通过，无任何回归。
