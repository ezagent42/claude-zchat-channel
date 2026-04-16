---
type: eval-doc
id: cs-eval-route-table
status: confirmed
producer: skill-5
created_at: "2026-04-16"
mode: simulate
feature: "路由表机制 + IRC 统一总线 + operator side inject"
submitter: yaosh
related:
  - cs-eval-architecture-refactor
---

# Eval: 路由表机制 + IRC 统一总线

## 基本信息
- 模式：模拟
- 提交人：yaosh
- 日期：2026-04-16
- 状态：draft

## 背景

R0-R7 重构完成后（server.py 779→201 行），业务逻辑已从 transport 层搬到 engine 层。
但核心问题未解决：**所有路由决策仍是 if/elif 硬编码**，channel-server 仍耦合业务逻辑。

三个待解决的设计问题：
1. 命令分发 / 消息类型分发 / 角色分发都是 if/elif 链
2. Operator side 消息不注入 agent（PRD 旅程 3 要求注入）
3. Agent dispatch 后不 join conversation channel（side 消息走 channel 需要 agent 在 channel 中）

## 重构范围（仅 channel-server）

| 子任务 | 目标 | 文件 |
|--------|------|------|
| T1 | 命令分发表驱动 | engine/command_handler.py |
| T2 | 消息类型分发表驱动 | engine/message_router.py |
| T3 | Bridge 入站角色分发表驱动 | feishu_bridge/bridge.py |
| T4 | Bridge 出站事件分发表驱动 | feishu_bridge/bridge.py |
| T5 | Operator side → IRC inject to agent | engine/message_router.py + command_handler.py |
| T6 | Agent dispatch → join #conv-{id} | engine/command_handler.py |
| T7 | Gate if/elif → dict lookup | zchat_protocol/gate.py (小改) |

## Testcase 表格

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 模拟效果 | 差异描述 | 优先级 |
|---|------|---------|---------|---------|---------|---------|--------|
| 1 | T1: 命令表驱动 — /hijack | command_handler.py 用 dict dispatch | operator 发 /hijack | `_OPERATOR_COMMANDS["hijack"](cmd, conv_id, op_id)` 执行 | 当前 if cmd.name == "hijack" 链（5 个 elif）。改为 `_OPERATOR_COMMANDS = {"hijack": _handle_mode_switch, "resolve": _handle_resolve, ...}`，execute 变为 `handler = self._op_commands.get(cmd.name); await handler(...)` | 消除 if/elif，新增命令只加字典条目 | P0 |
| 2 | T1: 命令表驱动 — admin /status | 同上 | admin 发 /status | `_ADMIN_COMMANDS["status"](cmd, admin_id)` 执行 | 当前 6 个 elif。改为 dict dispatch | 同上 | P0 |
| 3 | T2: 消息前缀表驱动 | message_router.py 用 dict dispatch | agent 回复 __edit:uuid:text | `_MSG_HANDLERS["edit"](conv_id, parsed)` 执行 | 当前 if parsed["type"] == "edit" 链（3 个分支）。改为 dict | 小改，3 个分支 | P1 |
| 4 | T3: Bridge 入站角色表驱动 | bridge.py _forward_to_bridge 用 dict | customer 消息到达 | `_ROLE_HANDLERS["customer"](chat_id, text, ...)` 执行 | 当前 if role == "customer" / elif "operator" / elif "admin"。改为 dict | 新增角色只加字典条目 | P0 |
| 5 | T4: Bridge 出站事件表驱动 | bridge.py _on_bridge_event 用 dict | reply 事件到达 | `_EVENT_HANDLERS["reply"](msg)` 执行 | 当前 8 个 if/elif。改为 dict | 同上 | P1 |
| 6 | T5: Operator side → agent inject | copilot 模式，operator 发建议 | operator 在 squad 群发 "建议强调优惠" | Gate 判定 side → IRC PRIVMSG #conv-{id} :@fast-agent __side:建议强调优惠 → agent 收到 | 当前 operator_message 经 Gate 后只发 send_reply(side)，不 inject agent。需在 message_router 中加：if visibility == side → 额外发 IRC @mention 到 conv channel | agent 能看到 operator 建议并采纳（PRD 旅程 3） | P0 |
| 7 | T5: Side 消息持久化 | 同上 | side 消息发到 #conv-{id} | cs-bot _on_pubmsg 捕获 → MessageStore.save(visibility=SIDE) | 当前 side 消息不走 IRC 所以不持久化。改为走 channel 后自动被 cs-bot 捕获并存储 | side 消息有审计记录 | P1 |
| 8 | T6: Agent dispatch → join channel | customer_connect 时 | auto-dispatch fast-agent | channel-server 通过 IRC 通知 agent join #conv-{id}：PRIVMSG @fast-agent :__zchat_sys:{"type":"sys.join_request","body":{"channel":"conv-{id}"}} | 当前只有 cs-bot join channel，agent 不 join。需在 handle_customer_connect 中给每个 dispatched agent 发 sys.join_request | agent 自动 join conv channel，能收到后续 @mention 消息 | P0 |
| 9 | T6: Agent 收到 join_request 后自动 join | agent_mcp 处理 sys.join_request | sys.join_request 到达 agent | agent_mcp 的 IRC transport handle_sys_message 已实现：收到 sys.join_request → conn.join(#channel) | 已实现在 transport/irc_transport.py:128-132。无需修改 agent 侧 | 无差异，已有 | P0 |
| 10 | T7: Gate dict lookup | gate.py 用 dict 替换 if/elif | copilot + operator → SIDE | GATE_RULES[(COPILOT, OPERATOR)] = SIDE | 当前 4 个 if/elif。改为 dict lookup，default 返回原始 visibility | 小改，纯机械替换 | P1 |
| 11 | 回归：Unit 测试 | T1-T7 完成 | pytest tests/unit/ feishu_bridge/tests/ | 258+ passed | 行为不变，只改内部分发方式 | 低风险 | P0 |
| 12 | 回归：E2E 测试 | T1-T7 完成 | pytest tests/e2e/ | 24 passed | callback 签名不变 | 低风险 | P0 |
| 13 | 新增：operator side inject E2E | T5+T6 完成 | operator_message in copilot → agent 收到 side → agent reply 采纳 | agent 回复中包含 operator 建议内容 | 当前无此测试。需新增 E2E test | 新功能测试 | P0 |

## 风险评估

### 低风险
- T1/T2/T3/T4/T7: 纯 if/elif → dict 替换，行为不变
- T9: agent 侧 sys.join_request 已实现

### 中风险
- T6: agent dispatch → sys.join_request 需要通过 IRC 发系统消息，时序需要验证（agent 是否已连接 IRC）
- T5: side 消息走 channel 需要 agent 已 join 该 channel（依赖 T6）

### 高风险
- 无。所有改动都是内部实现，不改外部接口

## 依赖关系

```
T7 (Gate dict) ── 无依赖
T1 (命令表) ── 无依赖
T2 (消息前缀表) ── 无依赖
T3 (入站角色表) ── 无依赖
T4 (出站事件表) ── 无依赖
T6 (Agent join channel) ── 无依赖
T5 (Side inject) ── 依赖 T6（agent 需先 join channel）
```

T1-T4 + T6 + T7 全部可并行。T5 最后做。

## 后续行动

- [ ] eval-doc 已注册到 .artifacts/eval-docs/
- [ ] 用户已确认 testcase 表格 (status: confirmed → confirmed)
