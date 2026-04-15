---
type: test-plan
id: cs-plan-gate-fix
status: executed
producer: skill-2
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-gate-fix
  - cs-diff-gate-fix
  - cs-report-gate-fix
---

# Test Plan: send_event capability 过滤 bug fix

## 来源

- eval-doc: `cs-eval-gate-fix`
- 代码改动范围：
  - `bridge_api/ws_server.py`：`send_event()` 新增 `target_capabilities: set[str] | None = None` 参数
  - `server.py`：`_on_sla_breach` 调用 `send_event` 时传入 `target_capabilities={"operator", "admin"}`

## 用例列表

### E2E tests (`tests/e2e/test_gate_enforcement.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-001 | test_side_message_not_received_by_customer | P0 | eval BEH-2/3 | customer_ws 不收到 side visibility 消息和 sla.breach 事件（此前 FAIL，修复后 PASS） |
| TC-002 | test_mode_changed_event_broadcast_to_all | P0 | eval BEH-1 | mode.changed 事件仍广播到所有连接（target_capabilities=None 的默认行为不变） |

### Unit tests（逻辑验证）

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-003 | send_event target_capabilities=None 广播到所有连接 | P1 | eval BEH-1 | 向后兼容：不传 target_capabilities 时所有连接收到事件 |
| TC-004 | send_event target_capabilities={"operator"} 过滤 customer 连接 | P1 | eval BEH-2 | 仅 operator capability 的连接收到事件，customer 连接被跳过 |

## 统计

- 总数：2 E2E（已有） + 2 unit（逻辑） = 4
- P0: 2 E2E
- P1: 2 unit

## 实现要点

1. **TC-001 / TC-002** 为已有 E2E 测试，无需新增代码，仅验证修复后全部 PASS
2. **TC-003 / TC-004** 为 `send_event` 方法的 unit 级逻辑验证：
   - 构造 mock BridgeConnection（分别持有 `["customer"]` 和 `["operator"]` capabilities）
   - 调用 `send_event` 并断言 `websocket.send` 被调用/未被调用

## 风险

- 无：改动范围极小（1 个参数 + 1 处调用），回归风险可忽略

## Merge 条件

- TC-001 / TC-002 E2E PASS
- 全量 195 tests PASS（0 回归）
