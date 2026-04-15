---
type: eval-doc
id: cs-eval-commands
status: confirmed
producer: skill-5
created_at: "2026-04-15"
mode: simulate
feature: "命令 Handler 补全 — /resolve + /status + /dispatch 接线到 bridge callback"
submitter: "yaosh"
related:
  - cs-plan-commands
  - cs-diff-commands
  - cs-report-commands
---

# Eval: 命令 Handler 补全 — /resolve /status /dispatch

## 基本信息
- 模式：模拟
- 提交人：yaosh
- 日期：2026-04-15
- 状态：confirmed

## 架构适配分析

三个命令的底层组件已全部 WORKING（ConversationManager.resolve/list_active/add_participant, SquadRegistry, EventBus）。
只需在 `server.py:wire_bridge_callbacks()` 中增加 bridge callback 接线：
- /resolve: 扩展 `_on_operator_command()`, 新增 CSAT 接收分支在 `_on_customer_message()`
- /status + /dispatch: 新建 `_on_admin_command()` 回调 + 注册到 `bridge_server.on_admin_command`

**影响范围**: 仅修改 server.py（~50 行新增），不修改 engine/ bridge_api/ protocol/。

## Testcase 表格

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 模拟效果 | 差异 | 优先级 |
|---|------|---------|---------|---------|---------|------|--------|
| 1 | /resolve 正常结案 | 活跃 conversation + operator 已加入 | operator 发 /resolve | conv_manager.resolve() 被调用 + conversation.resolved event + CSAT 邀请发出 | 直接调用 resolve() + send_event + send_reply | 无 | P0 |
| 2 | /resolve conversation 不存在 | conv_id 不匹配 | operator 发 /resolve | 静默跳过（现有模式） | conv is None → return | 无 | P0 |
| 3 | CSAT 评分接收 | conversation 已 resolve | customer_message 带 csat_score | set_csat() 被调用 | _on_customer_message 分支判断 csat_score | 无 | P0 |
| 4 | /status 查看活跃对话 | 至少 1 个活跃对话 | admin 发 /status | 格式化列表（conv_id + state + mode + participants）返回 | list_active() + 格式化 + send_reply visibility=system | 无 | P0 |
| 5 | /status 无活跃对话 | 空列表 | admin 发 /status | "无活跃对话" 消息 | list_active() 返回 [] | 无 | P1 |
| 6 | /dispatch agent 到对话 | 活跃对话 + agent nick 存在 | admin 发 /dispatch conv_id agent | add_participant() + agent.dispatched event | 添加 AGENT 角色参与者 + send_event | 无 | P1 |
| 7 | /dispatch conversation 不存在 | conv_id 不匹配 | admin 发 /dispatch | 静默跳过 | conv is None → return | 无 | P1 |
| 8 | admin_command 回调注册 | wire_bridge_callbacks() 执行后 | 检查 bridge_server.on_admin_command | 不为 None | 与 on_operator_join/command 同模式注册 | 无 | P0 |
| 9 | customer_message 回调注册 | wire_bridge_callbacks() 执行后 | 检查 bridge_server.on_customer_message | 不为 None（含 CSAT 分支） | 新增回调注册 | 无 | P0 |
| 10 | unknown operator command 静默跳过 | 正常 conversation | 发 /unknown_cmd | 不 crash，静默跳过 | target_mode is None → return（现有逻辑） | 无 | P1 |
| 11 | E2E: /resolve via WebSocket | 真实 BridgeAPIServer + ConversationManager | WS client 发 operator_command /resolve | 收到 conversation.resolved event | WebSocket 端到端验证 | 无 | P0 |
| 12 | E2E: /status via WebSocket | 真实 BridgeAPIServer + ConversationManager | WS client 发 admin_command /status | 收到 system reply | WebSocket 端到端验证 | 无 | P0 |
| 13 | E2E: /dispatch via WebSocket | 真实 BridgeAPIServer + ConversationManager | WS client 发 admin_command /dispatch | 收到 agent.dispatched event | WebSocket 端到端验证 | 无 | P1 |

## 后续行动

- [x] eval-doc 已注册
- [x] 用户已确认 (status: confirmed)
