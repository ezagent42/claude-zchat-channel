---
type: eval-doc
id: cs-eval-p2-commands
status: confirmed
mode: simulate
feature: P2 命令 handler — /abandon /assign /reassign /squad
producer: skill-5
submitter: yaosh
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-plan-p2-commands
  - cs-diff-p2-commands
  - cs-report-p2-commands
---

# Eval: P2 命令 handler — /abandon /assign /reassign /squad

## 背景

Phase 4 完成了 P0 + P1 命令（hijack / release / copilot / resolve / status / dispatch / review）。
Spec `08-commands.md` 还定义了 4 个 P2 命令，底层 SquadRegistry / ConversationManager API
均已 WORKING，仅缺 wire_bridge_callbacks 中的 handler 接线。

需求来源：`docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.6。

## 行为预期

| 命令 | 角色 | 语法 | 行为 |
|------|------|------|------|
| `/abandon` | operator | `/abandon` | 当前对话 close（不调 resolve，不发 CSAT），发 `conversation.closed` event + system reply |
| `/assign` | admin | `/assign <agent_nick> <operator_id>` | SquadRegistry.assign(agent, operator)，发 `squad.assigned` event + system reply |
| `/reassign` | admin | `/reassign <agent_nick> <from_op> <to_op>` | SquadRegistry.reassign(agent, to_op)，发 `squad.reassigned` event + system reply |
| `/squad` | admin | `/squad [operator_id]` | 列出某 operator 的 agent 分队（不指定时列全部分队） |

## Testcase（simulate 模拟）

| # | 场景 | 前置条件 | 操作 | 预期 | 模拟效果 | 差异 | 优先级 |
|---|------|---------|------|------|---------|------|--------|
| TC-1 | /abandon 关闭活跃对话 | conv 已 active，operator 已 join | operator 发 `/abandon` | conv.state=closed，发 conversation.closed event，system reply 通知 | OK，需先 transition CREATED→ACTIVE 再 close（参考 /resolve handler） | 无差异 | P0 |
| TC-2 | /abandon 不发 CSAT | conv active | operator 发 `/abandon` | 不发 csat_request 卡片，不调 resolve | OK，与 /resolve 区分清楚 | 无差异 | P0 |
| TC-3 | /assign 新增分队 | SquadRegistry 空 | admin `/assign agent0 op1` | get_operator(agent0)==op1，squad.assigned event 含 agent_nick + operator_id | OK | 无差异 | P1 |
| TC-4 | /assign 覆盖已有分队 | agent0→op1 | admin `/assign agent0 op2` | get_operator(agent0)==op2，旧 op1 squad 不再含 agent0，发 squad.reassigned event（or assigned） | 决策：发 `squad.assigned`（reassign 是显式语义），不发 reassigned | 需在 spec 内确认；测试中接受 squad.assigned | P1 |
| TC-5 | /reassign 显式迁移 | agent0→op1 | admin `/reassign agent0 op1 op2` | get_operator==op2，squad.reassigned event 含 from/to | OK | 无差异 | P1 |
| TC-6 | /squad 列出全分队 | 多个 assign | admin `/squad` | system reply 列出每个 operator 及其 agent 列表 | OK，需读 SquadRegistry 内部 dict（无现成 list_all() API） | 实现里加 list_all 或直接读 `_operator_to_agents.copy()` | P2 |
| TC-7 | /squad 列单 operator | op1 有 agent0/agent1 | admin `/squad op1` | system reply: "op1: agent0, agent1" | OK | 无差异 | P2 |
| TC-8 | /squad 空分队 | 无 assign | admin `/squad` | system reply: "[squad] 暂无分队" | OK | 无差异 | P2 |

## 风险

- **TC-1 边界**：CREATED 状态对话能否 /abandon？参考 /resolve handler 的写法（先 activate）。
- **TC-4 决策**：覆盖式 assign 究竟发哪种 event？`squad.assigned`（与 SquadRegistry 实际行为一致：assign 内部调 reassign）。
- **SquadRegistry 缺 list_all**：需补一个 `list_all() -> dict[str, list[str]]` 方法，或在 handler 里直接读 `_operator_to_agents`（不优雅，倾向加 list_all）。

## 验证范围

- 覆盖：unit 8 个用例（TC-1~8），E2E 2 个用例（/abandon + /assign+/squad 链路）。
- 不覆盖：飞书 card 反映 squad 变化（属于 4.6.5 后续工作）。
