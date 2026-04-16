---
id: cs-eval-protocol
type: eval-doc
status: confirmed
phase: "Phase 1: Protocol"
created_at: "2026-04-14"
---

# cs-eval-protocol — 通用对话协议原语层

## 特性描述

实现 channel-server 的 `protocol/` 模块——纯 Python 无外部依赖的协议原语层。
包含 Conversation 状态机、Participant 角色、Mode 转换、Message Gate 门控、
Event 事件类型、Timer 计时器、Commands 命令解析共 8 个模块。

## 期望行为

| 模块 | 关键行为 |
|------|---------|
| conversation.py | 创建对话 → CREATED；状态转换遵守合法路径；非法转换抛 ValueError |
| participant.py | 4 种角色 (customer/agent/operator/observer) 互不相同 |
| mode.py | 6 条合法转换路径；auto→auto 等非法转换抛 ValueError |
| gate.py | COPILOT 模式 operator PUBLIC→SIDE；TAKEOVER 模式 agent PUBLIC→SIDE；SIDE/SYSTEM 不变 |
| event.py | Event 自动生成 UUID；EventType 枚举覆盖 spec §7 全部类型 |
| commands.py | /hijack 解析为 name=hijack；/dispatch 解析位置参数；非命令返回 None；未知命令 name=unknown |
| timer.py | Timer + TimerAction 数据结构完整 |
| message_types.py | Message + MessageVisibility 枚举 (PUBLIC/SIDE/SYSTEM) |

## 设计约束

- 纯 Python stdlib，无外部依赖
- 所有模块是无状态纯逻辑，不涉及 I/O
- Gate 是 mechanism 级保证，不可绕过
- 状态转换表驱动，而非硬编码条件判断

## Spec 参考

`spec/channel-server/01-protocol-primitives.md` §1-§10
