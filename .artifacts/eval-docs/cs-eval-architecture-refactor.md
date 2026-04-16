---
type: eval-doc
id: cs-eval-architecture-refactor
status: confirmed
producer: skill-5
created_at: "2026-04-16"
mode: simulate
feature: "channel-server 架构重构 — 恢复 spec 三层分离"
submitter: yaosh
related:
  - cs-eval-arch-review
  - cs-eval-prerelease-infra
---

# Eval: channel-server 架构重构 — 恢复 spec 三层分离

## 基本信息
- 模式：模拟
- 提交人：yaosh
- 日期：2026-04-16
- 状态：draft

## 背景

Spec 定义了清晰的三层架构：
```
Transport (IRC/WebSocket)  — 只管收发，零业务逻辑
Protocol Engine (engine/)  — 状态机/Gate/EventBus，协议强制执行
Application (Bridge/Plugin) — 渠道适配 + 业务行为
```

实际实现中，`server.py`（779 行）成为业务逻辑的垃圾场，命令处理、
Gate 调用、统计聚合、升级路由全部塞在 transport 层的 callback 里。
Pre-release 测试暴露了多个链路断裂（消息转发缺失、IRC nick collision、
conversation 创建缺失），根因都是层间耦合导致职责不清。

## 重构范围

| 子任务 | 目标 | 影响文件 |
|--------|------|---------|
| R0 | Protocol 原语迁移到 zchat-protocol submodule | protocol/*.py → zchat-protocol/zchat_protocol/ |
| R1 | 提取 CommandHandler 到 engine/ | server.py → engine/command_handler.py |
| R2 | 统一 Gate 调用 | server.py, zchat_protocol/gate.py |
| R3 | 提取 MessageRouter 到 engine/ | server.py → engine/message_router.py |
| R4 | 分离 VisibilityRouter | feishu_bridge/visibility_router.py → engine/ + bridge/ |
| R5 | Bridge API 入站转发 | feishu_bridge/bridge.py, bridge_api_client.py |
| R6 | Agent 消息协议规范化 | agent_mcp.py, transport/irc_transport.py |
| R7 | server.py 瘦身为纯胶水 | server.py |

## Testcase 表格

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 模拟效果 | 差异描述 | 优先级 |
|---|------|---------|---------|---------|---------|---------|--------|
| 0a | R0: Protocol 原语迁移 | zchat-protocol submodule 可编辑 | 将 channel-server/protocol/ 下 7 个模块迁移到 zchat-protocol | `from zchat_protocol.conversation import Conversation, ConversationState`; `from zchat_protocol.gate import gate_message` 等 | 当前 channel-server/protocol/ 有 7 个模块（conversation, mode, gate, event, commands, participant, message_types, timer），zchat-protocol 只有 naming + sys_messages。迁移后 channel-server 删除 protocol/ 目录，全部从 zchat_protocol import | channel-server 不再自带 protocol 原语，统一由 zchat-protocol 提供 | P0 |
| 0b | R0: Import 全局替换 | R0a 完成 | channel-server 所有 `from protocol.xxx import` 替换为 `from zchat_protocol.xxx import` | 全项目编译通过，238 unit + 24 E2E 不变 | 当前 channel-server 有 ~30 处 `from protocol.xxx import`。zchat-protocol 已是 submodule，pip install -e 可用。替换是纯机械操作 | 搜索替换 + 验证 | P0 |
| 0c | R0: zchat-protocol 版本号 | zchat-protocol pyproject.toml | PROTOCOL_VERSION 从 "0.1" 升到 "0.2"，包含新增模块 | zchat-protocol 0.2 包含：naming, sys_messages, conversation, mode, gate, event, commands, participant, message_types, timer | 当前 0.1 只有 naming + sys_messages。升级后包含完整协议原语 | 新增 7 个模块 | P0 |
| 1 | R1: /hijack 命令执行 | engine/command_handler.py 存在 | operator 发送 /hijack | CommandHandler.execute("hijack", conv_id, operator_id) → ModeManager 转换 → EventBus 发布 mode.changed → Bridge API 广播 | 当前 server.py:178-211 inline 实现，直接调 conv_manager + mode_manager + bridge_server。提取后 server.py 只调 `await cmd_handler.execute(cmd, conv_id, operator_id)` 一行 | server.py 从 30 行命令处理缩减为 1 行调用 | P0 |
| 2 | R1: /resolve 命令执行 | CommandHandler 含 resolve 逻辑 | operator 发送 /resolve | CommandHandler 关闭 conversation → 发 conversation.closed 事件 → 可选 CSAT 请求 | 当前 server.py:156-176。提取后同样一行调用 | 同上 | P0 |
| 3 | R1: /status /dispatch /review | CommandHandler 含 admin 命令 | admin 发送 /status | CommandHandler 查询 ConversationManager + 格式化输出 → 返回 text | 当前 server.py:236-303 含 SQL 聚合。提取后 cmd_handler 调 EventBus.query() | 统计逻辑从 transport 移到 engine | P0 |
| 4 | R2: Gate 签名统一 | gate_message() 只有一种调用方式 | operator 消息经过 Gate | gate_message(conv, Participant, MessageVisibility) → MessageVisibility | 当前两处调用：server.py:420 用 string，server.py:734 用对象。统一后全用对象签名 | 消除 runtime 类型不一致风险 | P0 |
| 5 | R3: Agent 消息路由 | engine/message_router.py 统一处理 | agent 回复 → cs-bot 收到 | MessageRouter.route_agent_message(nick, body, conv_id) → parse prefix → Gate → Bridge API send_reply | 当前 server.py:684-724 _route_irc_messages + _on_privmsg 分散。统一后一个 Router 处理所有入站消息（pubmsg + privmsg） | 消除 pubmsg/privmsg 两条路径的重复逻辑 | P0 |
| 6 | R3: Customer 消息路由 | MessageRouter 处理 customer_message | customer_message 到达 → 转发给 agent | MessageRouter.route_customer_message(conv_id, text) → activate conv → PRIVMSG to agent nicks | 当前 server.py:434-475 _on_customer_message inline。提取后 Router 封装 IRC 转发细节 | transport 层不直接操作 IRC | P0 |
| 7 | R4: VisibilityRouter 分离 | engine/visibility_gate.py（通用路由规则）+ feishu_bridge/feishu_renderer.py（飞书渲染） | reply 事件带 visibility=side | engine 决定 target_roles={operator,admin}，Bridge renderer 只做 send_text/reply_in_thread | 当前 feishu_bridge/visibility_router.py 同时做 visibility 决策 + Feishu card 渲染。分离后 engine 给出 "发给谁"，bridge 只管 "怎么发" | Bridge 不再包含 protocol 级 visibility 逻辑 | P1 |
| 8 | R4: Card 渲染独立 | feishu_renderer.py 负责 card 构建 | conversation.created → squad 群收到 card | FeishuRenderer.render_card(conv, metadata) → JSON card dict | 当前 visibility_router.py:208-274 _build_conv_card 嵌在 Router 里。分离后只在 renderer | Feishu 特定 UI 不污染通用路由 | P1 |
| 9 | R5: Bridge 入站转发完整 | bridge.py _on_message → _forward_to_bridge | 飞书用户发消息 → feishu_bridge WSS 收到 → Bridge API customer_message | bridge.py 正确转发 customer/operator/admin 消息，bridge_api_client.py 连接 + 注册 + 收发 | 当前实现存在但 pre-release 测试暴露了注册缺失（已修）、conversation 查找缺失（已修 oc_ fallback）。需验证全链路 | 需要 E2E 验证 | P0 |
| 10 | R6: Agent IRC 前缀协议文档化 | spec 中定义 __msg:/__edit:/__side: 格式 | agent 调用 reply(edit_of=msg_id) | agent_mcp 构造 __edit:{msg_id}:{text}，IRC transport 解析为 edit 事件 | 当前前缀协议散在 agent_mcp.py:200-219 + transport/irc_transport.py:30-62，无 spec 文档。需正式写入 spec | 前缀格式有规范，但只在代码中，不在 spec | P1 |
| 11 | R6: IRC nick collision | 文件锁防止多实例 | Claude Code 启动 2 个 MCP 实例 | 只有 1 个实例连 IRC，另一个 skip | 当前已实现 fcntl 文件锁（agent_mcp.py）。需验证稳定性 | 已实现，需 E2E 验证 | P0 |
| 12 | R7: server.py 瘦身 | server.py < 200 行 | 完成 R1-R6 后 | server.py 只做：build_components() + wire callbacks + start event loop。所有业务逻辑在 engine/ | 当前 779 行。提取 R1(200行) + R3(100行) + R4(50行) 后约 400 行。再提取 admin 命令可到 200 行 | 从 779 行减到 ~150 行 | P1 |
| 13 | 回归：Unit 测试不变 | 238 unit tests | 重构后跑 pytest tests/unit/ feishu_bridge/tests/ | 238+ passed, 0 failed | 所有 engine/ 组件有独立 unit test，重构不改行为只改位置。gate_message 签名修复可能需要更新 test_operator_message 等 | 可能需更新 5-10 个 test 的调用方式 | P0 |
| 14 | 回归：E2E 测试不变 | 24 E2E tests | 重构后跑 pytest tests/e2e/ | 24 passed, 0 failed | server.py callback 签名不变，只是内部实现提取到 engine/。E2E 通过 Bridge API 测试，不直接调 engine | 低风险 | P0 |
| 15 | 新架构可测试性 | CommandHandler 可独立 unit test | 直接调用 CommandHandler 不需要 server.py | cmd = CommandHandler(conv_manager, mode_manager, event_bus); await cmd.execute(Command("hijack"), conv_id, "op1") | 当前命令逻辑嵌在 callback 中无法独立测试。提取后可写 10+ unit tests | 提升可测试性 | P1 |

## 风险评估

### 高风险
- **Gate 签名修复**：server.py:420 的 `gate_message(conv.mode, "operator", "public")` 调用当前"碰巧工作"（因为 gate.py 可能有兼容处理）。修复后需要确认所有 test 通过
- **server.py 回调签名**：E2E 测试直接构造 bridge callback，如果 callback 签名变了 E2E 会断

### 中风险
- **VisibilityRouter 分离**：feishu_bridge 的 card+thread 模型紧耦合在 VisibilityRouter 里，分离需要仔细设计 interface
- **IRC 前缀协议**：agent_mcp 和 transport 的前缀解析逻辑需要保持一致

### 低风险
- **MessageRouter 提取**：纯移动代码，逻辑不变
- **CommandHandler 提取**：纯移动代码 + 统一签名

## 依赖关系

```
R0 (Protocol 迁移) ── 无依赖，最先做（所有后续步骤的 import 基础）
R2 (Gate 统一) ── 依赖 R0
R6 (Agent 协议) ── 依赖 R0
R1 (CommandHandler) ── 依赖 R0 + R2
R3 (MessageRouter) ── 依赖 R0 + R2
R4 (VisibilityRouter) ── 依赖 R3
R5 (Bridge 入站) ── 依赖 R3
R7 (server.py 瘦身) ── 依赖 R1+R3+R4
```

建议执行顺序：R0 → R2 → R6 → R1 → R3 → R4 → R5 → R7

### 跨仓库操作
- R0 在 zchat-protocol submodule 中操作（独立 commit + push）
- R1-R7 在 zchat-channel-server 中操作
- R0 完成后 channel-server 更新 submodule 引用

## 后续行动

- [ ] eval-doc 已注册到 .artifacts/eval-docs/
- [ ] 用户已确认 testcase 表格 (status: confirmed → confirmed)
