---
type: test-plan
id: cs-plan-commands
status: confirmed
producer: skill-2
created_at: "2026-04-15"
trigger: "eval-doc cs-eval-commands + spec 06-gap-fixes.md 待实现表"
related:
  - cs-eval-commands
---

# Test Plan: 命令 Handler 补全 — /resolve /status /dispatch

## 触发原因

06-gap-fixes.md 待实现表中 3 个 P0/P1 命令（/resolve /status /dispatch）阻塞 Phase Final。
底层组件已 WORKING，只需 server.py bridge callback 接线。

## 测试文件分布

| 文件 | 用例数 | 类型 |
|------|--------|------|
| tests/unit/test_command_handlers.py | 10 | Unit (mock) |
| tests/e2e/test_command_handlers.py | 3 | E2E (WebSocket) |

## Unit Tests (tests/unit/test_command_handlers.py)

### TC-001: /resolve 正常结案
- **来源**: eval-doc #1
- **优先级**: P0
- **前置条件**: 活跃 conversation + mock 组件
- **步骤**: 调用 _on_operator_command(msg, cmd(name="resolve"))
- **预期**: conv_manager.resolve() 被调用 + bridge_server.send_event("conversation.resolved") + send_reply(CSAT 邀请, visibility="public")

### TC-002: /resolve conversation 不存在
- **来源**: eval-doc #2
- **优先级**: P0
- **步骤**: conv_id 不匹配 → _on_operator_command
- **预期**: 静默返回，resolve() 不被调用

### TC-003: CSAT 评分接收
- **来源**: eval-doc #3
- **优先级**: P0
- **步骤**: _on_customer_message(msg with csat_score=5)
- **预期**: conv_manager.set_csat(conv_id, 5) 被调用

### TC-004: /status 有活跃对话
- **来源**: eval-doc #4
- **优先级**: P0
- **步骤**: 调用 _on_admin_command(msg, cmd(name="status"))
- **预期**: list_active() 被调用 + send_reply 包含格式化文本 (visibility="system")

### TC-005: /status 无活跃对话
- **来源**: eval-doc #5
- **优先级**: P1
- **步骤**: list_active() 返回空列表
- **预期**: send_reply 包含 "无活跃对话"

### TC-006: /dispatch 正常分派
- **来源**: eval-doc #6
- **优先级**: P1
- **步骤**: 调用 _on_admin_command(msg, cmd(name="dispatch", args={conv_id, agent}))
- **预期**: add_participant(AGENT) 被调用 + send_event("agent.dispatched")

### TC-007: /dispatch conversation 不存在
- **来源**: eval-doc #7
- **优先级**: P1
- **步骤**: conv_id 不匹配
- **预期**: 静默返回

### TC-008: admin_command 回调注册
- **来源**: eval-doc #8
- **优先级**: P0
- **步骤**: wire_bridge_callbacks() → 检查 bridge_server.on_admin_command
- **预期**: 不为 None

### TC-009: customer_message 回调注册
- **来源**: eval-doc #9
- **优先级**: P0
- **步骤**: wire_bridge_callbacks() → 检查 bridge_server.on_customer_message
- **预期**: 不为 None

### TC-010: unknown command 静默跳过
- **来源**: eval-doc #10
- **优先级**: P1
- **步骤**: _on_operator_command(msg, cmd(name="unknown_thing"))
- **预期**: 无 crash，resolve/transition 不被调用

## E2E Tests (tests/e2e/test_command_handlers.py)

### TC-011: E2E /resolve via WebSocket
- **来源**: eval-doc #11
- **优先级**: P0
- **步骤**: WS 发 customer_connect → 创建 conversation → WS 发 operator_command /resolve → 收消息
- **预期**: 收到 conversation.resolved event + CSAT 邀请 reply

### TC-012: E2E /status via WebSocket
- **来源**: eval-doc #12
- **优先级**: P0
- **步骤**: WS 发 customer_connect → WS 发 admin_command /status → 收消息
- **预期**: 收到 system reply 包含 conversation 列表

### TC-013: E2E /dispatch via WebSocket
- **来源**: eval-doc #13
- **优先级**: P1
- **步骤**: WS 发 customer_connect → WS 发 admin_command /dispatch conv_id agent → 收消息
- **预期**: 收到 agent.dispatched event

## 统计

| 指标 | 值 |
|------|-----|
| 总用例 | 13 |
| P0 | 8 |
| P1 | 5 |
| Unit | 10 |
| E2E | 3 |
