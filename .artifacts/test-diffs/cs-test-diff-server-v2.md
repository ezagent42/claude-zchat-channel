---
type: test-diff
id: cs-test-diff-server-v2
status: executed
producer: skill-3
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-plan-server-v2
  - cs-diff-server-v2
  - cs-report-server-v2
evidence: []
---

# Test Diff: Phase 4 补全 — mode switching + gate enforcement

## Source
- Test plan: cs-plan-server-v2 (`.artifacts/test-plans/cs-plan-server-v2.md`)

## Changes

### New test cases

| 文件 | 函数 | TC-ID | 层级 | 验证内容 |
|------|------|-------|------|---------|
| `tests/unit/test_bridge_api.py`（追加） | `test_on_operator_join_callback_invoked` | TC-U12 | unit | `on_operator_join` 槽已声明，默认 None |
| `tests/unit/test_bridge_api.py`（追加） | `test_send_event_broadcasts_to_all_connections` | TC-U13 | unit | `send_event()` 广播到所有 WS 连接 |
| `tests/unit/test_bridge_api.py`（追加） | `test_send_event_no_connections_noop` | TC-U14 | unit | `send_event()` 无连接时不抛异常 |
| `tests/unit/test_server_integration.py`（追加） | `test_build_components_injects_operator_callbacks` | TC-U15 | unit | `wire_bridge_callbacks()` 注入 on_operator_join + on_operator_command |
| `tests/e2e/test_mode_switching.py`（新建） | `test_operator_join_triggers_copilot` | TC-E05 | E2E | operator_join → mode.changed(auto→copilot) 广播 |
| `tests/e2e/test_mode_switching.py`（新建） | `test_hijack_triggers_takeover` | TC-E06 | E2E | /hijack → mode.changed(copilot→takeover) 广播 |
| `tests/e2e/test_gate_enforcement.py`（新建） | `test_side_message_not_received_by_customer` | TC-E07 | E2E | side 消息不到 customer bridge |
| `tests/e2e/test_gate_enforcement.py`（新建） | `test_mode_changed_event_broadcast_to_all` | TC-E08 | E2E | mode.changed event 广播到全部 bridge |

**新增合计：4 unit + 4 E2E = 8 条**

### New fixtures

| 文件 | 名称 | Scope | 提供 |
|------|------|-------|------|
| `tests/e2e/test_gate_enforcement.py`（文件内） | `customer_ws` | function | 仅 customer capability 的 Bridge WS |
| `tests/e2e/test_gate_enforcement.py`（文件内） | `operator_ws` | function | 仅 operator capability 的 Bridge WS |

（两个 fixture 局部于文件，不放 conftest.py，因为只有 gate_enforcement 需要分角色 WS。）

### Modified files

- `tests/unit/test_bridge_api.py`：追加 3 个 unit 测试（TC-U12/13/14）
- `tests/unit/test_server_integration.py`：追加 1 个 unit 测试（TC-U15）
- `tests/e2e/test_mode_switching.py`：新建，2 条 E2E
- `tests/e2e/test_gate_enforcement.py`：新建，2 条 E2E

## Validation

- [x] 语法：所有文件为合法 Python（函数签名 / indent 已目测核查）
- [x] 导入：仅依赖 stdlib + pytest + websockets（已安装）
- [x] Fixture 图：无循环依赖；`customer_ws/operator_ws` → `channel_server` → `ergo_server`
- [x] 命名：`test_{actor}_{behavior}` 惯例
- [x] 无硬编码端口/路径：通过 `e2e_ports` fixture 获取
- [ ] 实现：待 Step 4 实现 `send_event()` + `wire_bridge_callbacks()` 后可运行
