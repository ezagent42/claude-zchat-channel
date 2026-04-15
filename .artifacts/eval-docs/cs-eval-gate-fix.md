---
type: eval-doc
id: cs-eval-gate-fix
status: confirmed
mode: verify
producer: skill-5
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-plan-gate-fix
  - cs-diff-gate-fix
  - cs-report-gate-fix
---

# Eval-doc: send_event capability 过滤 — sla.breach 不广播到 customer bridge

| 字段 | 值 |
|------|-----|
| ID | cs-eval-gate-fix |
| 类型 | eval-doc |
| 模式 | verify |
| 状态 | confirmed |
| 产出者 | skill-5 |
| 消费者 | skill-2 |
| 创建时间 | 2026-04-15T20:00:00Z |

## 背景

`test_side_message_not_received_by_customer` E2E 测试失败：customer_ws 收到了不该收到的消息。

**根因**：`bridge_api/ws_server.py` 的 `send_event()` 方法对所有已连接的 WebSocket 进行无差别广播。当 `plugins/sla_app.py` 设置的 3s onboard SLA timer 在测试执行期间触发 breach 时，`_on_sla_breach` 调用 `send_event("sla.breach", ...)` 将告警事件发送到了包括 customer bridge 在内的所有连接。Customer bridge 不应接收运营级 SLA 告警。

**修复方案**：为 `send_event()` 添加 `target_capabilities` 参数，按连接的 capability 集合过滤接收者。`_on_sla_breach` 指定 `target_capabilities={"operator", "admin"}`，使 sla.breach 事件仅发送到运营/管理端连接。

## 行为预期

### BEH-1: send_event 默认广播行为不变

- **触发**: `send_event()` 调用时 `target_capabilities=None`（默认值）
- **预期**: 事件发送到所有已连接的 WebSocket，与修改前行为一致
- **验证**: `test_mode_changed_event_broadcast_to_all` 继续 PASS

### BEH-2: send_event 按 capability 过滤

- **触发**: `send_event()` 调用时传入 `target_capabilities={"operator", "admin"}`
- **预期**: 仅 capabilities 包含 "operator" 或 "admin" 的连接收到事件；customer bridge（capability 不含 operator/admin）不收到
- **验证**: `test_side_message_not_received_by_customer` PASS

### BEH-3: sla.breach 事件不到达 customer bridge

- **触发**: SLA onboard timer 超时触发 `_on_sla_breach`
- **预期**: sla.breach 事件仅发送到 operator/admin bridge 连接
- **验证**: customer_ws 在超时窗口内不收到 sla.breach 事件

### BEH-4: 无 capability 匹配时无连接收到事件

- **触发**: `send_event()` 调用时 `target_capabilities={"nonexistent_role"}`
- **预期**: 没有任何连接收到事件
- **验证**: 无异常抛出，静默跳过

## 约束

- 仅修改 2 个文件：`bridge_api/ws_server.py`、`server.py`
- 不改动 protocol/engine/plugins/transport 层
- 回归：全部已有测试继续 PASS

## 关联 Artifact

- test-plan: cs-plan-gate-fix
- code-diff: cs-diff-gate-fix
- e2e-report: cs-report-gate-fix
