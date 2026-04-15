---
type: code-diff
id: cs-diff-p2-commands
status: confirmed
producer: skill-3
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-p2-commands
  - cs-plan-p2-commands
  - cs-report-p2-commands
---

# Code Diff: Task 4.6.6 — P2 命令 handler 接线

## 来源

- plan: `cs-plan-p2-commands`
- eval-doc: `cs-eval-p2-commands`
- 背景：Phase 4.6 架构拆分后，SquadRegistry / ConversationManager 底层能力已 WORKING，仅需在
  `wire_bridge_callbacks` 内接入 handler 完成 spec `08-commands.md` 中的 4 个 P2 命令。

## 变更文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `server.py` | M | `_on_operator_command` 新增 `/abandon`；`_on_admin_command` 新增 `/assign /reassign /squad`；`build_components` 注入 `SquadRegistry` |
| `engine/squad_registry.py` | M | 新增 `list_all()` 方法返回 `{operator_id: [agent_ids]}` 快照 |
| `protocol/commands.py` | M | `/squad` 增加可选 `target` 参数 |
| `tests/unit/test_p2_commands.py` | A | 10 个 unit 用例（TC-001~TC-010） |
| `tests/e2e/test_p2_commands.py` | A | 2 个 E2E 用例（TC-E01 abandon flow, TC-E02 assign+squad） |

## 改动类型

### /abandon（operator 命令）
- CREATED → ACTIVE → CLOSED 流程（参考 `/resolve`）
- 发布 `EventType.CONVERSATION_CLOSED` 到 EventBus（data 含 `abandoned_by`）
- 向 bridge 发 `conversation.closed` event + system reply "对话已被 {op} 放弃"
- **不**发 CSAT 卡片，**不**标 outcome（与 `/resolve` 区分）

### /assign（admin 命令）
- 调用 `SquadRegistry.assign(agent, operator)`
- 发 `EventType.SQUAD_ASSIGNED` + bridge `squad.assigned` event + system reply
- 覆盖语义：第二次 assign 同一 agent 会 detach 原 operator（依赖 SquadRegistry 内部行为）

### /reassign（admin 命令）
- 调用 `SquadRegistry.reassign(agent, to_op)`
- 发 `EventType.SQUAD_REASSIGNED`（含 `from_operator`, `to_operator`）+ bridge event + reply

### /squad（admin 命令）
- 无参数：`SquadRegistry.list_all()` → 多行文本 `[squad] 全部分队: ...`
- 指定 operator：`SquadRegistry.get_squad(op)` → `[squad] op: agent0, agent1`
- 空分队：`[squad] 暂无分队`

### SquadRegistry.list_all()
- 返回 `{op: [agents]}` 的深拷贝快照，外部修改不影响内部状态

## 影响模块

- Channel server command handlers (`server.py`)
- SquadRegistry API 扩展（`engine/squad_registry.py`）
- 命令解析（`protocol/commands.py`）
- 测试套件（unit + E2E）

## 风险评估

- **低风险**：SquadRegistry 所有操作已有 unit 覆盖；`/abandon` 参考 `/resolve` 的 CREATED→ACTIVE→close 模式。
- 不影响已有的 P0/P1 命令 handler。
- EventType.SQUAD_ASSIGNED / SQUAD_REASSIGNED 在 `protocol/event.py` 中已存在，无需新增。
