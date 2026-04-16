---
id: cs-plan-protocol
type: test-plan
status: confirmed
phase: "Phase 1: Protocol"
created_at: "2026-04-14"
related_ids:
  - cs-eval-protocol
---

# cs-plan-protocol — Protocol 模块测试计划

## 测试文件清单

| 文件 | 测试数 | 覆盖模块 |
|------|--------|---------|
| test_conversation.py | 6 | conversation.py — 状态机 + Resolution |
| test_participant.py | 3 | participant.py — 角色枚举 |
| test_mode.py | 5 | mode.py — 模式转换 |
| test_gate.py | 7 | gate.py + message_types.py — 消息门控 |
| test_event.py | 2 | event.py — 事件创建 + 类型完整性 |
| test_commands.py | 5 | commands.py — 命令解析 |

**总计: 28 个测试用例**

## 测试用例

### TC-01 ~ TC-06: Conversation 状态机
- TC-01: create_conversation 返回 CREATED 状态
- TC-02: CREATED → ACTIVE 合法
- TC-03: ACTIVE → IDLE → ACTIVE 循环合法
- TC-04: ACTIVE → CLOSED 直接关闭合法
- TC-05: CREATED → IDLE 非法，抛 ValueError
- TC-06: ConversationResolution 属性正确

### TC-07 ~ TC-09: Participant 角色
- TC-07: customer 角色创建
- TC-08: agent 角色创建
- TC-09: 4 种角色互不相同

### TC-10 ~ TC-14: Mode 转换
- TC-10: auto → copilot 合法
- TC-11: copilot → takeover 合法，记录 trigger
- TC-12: takeover → auto 合法
- TC-13: auto → auto 非法
- TC-14: 合法转换表恰好 6 条

### TC-15 ~ TC-21: Message Gate（核心）
- TC-15: AUTO 模式 agent PUBLIC 通过
- TC-16: COPILOT 模式 operator PUBLIC 降级为 SIDE
- TC-17: COPILOT 模式 agent PUBLIC 通过
- TC-18: TAKEOVER 模式 agent PUBLIC 降级为 SIDE
- TC-19: TAKEOVER 模式 operator PUBLIC 通过
- TC-20: SIDE 不升级
- TC-21: SYSTEM 不受影响

### TC-22 ~ TC-23: Event
- TC-22: Event 自动生成 UUID
- TC-23: EventType 枚举完整（8 个关键类型）

### TC-24 ~ TC-28: Commands
- TC-24: /hijack 解析
- TC-25: /dispatch 位置参数解析
- TC-26: /assign 位置参数解析
- TC-27: 非命令返回 None
- TC-28: 未知命令返回 name=unknown
