---
type: test-plan
id: cs-plan-architecture-refactor
status: confirmed
producer: skill-2
created_at: "2026-04-16"
trigger: "cs-eval-architecture-refactor (confirmed) — 架构重构三层分离"
related:
  - cs-eval-architecture-refactor
  - cs-eval-arch-review
---

# Test Plan: channel-server 架构重构验证

## 触发原因

eval-doc `cs-eval-architecture-refactor` 确认了 8 个重构子任务（R0-R7）。
本计划覆盖每个子任务的验证点，确保重构不改变行为只改变位置。

核心原则：**所有现有 238 unit + 24 E2E 测试必须不变通过**，同时新增针对提取模块的独立 unit test。

## 用例列表

### TC-001: R0 — zchat-protocol import 替换后编译通过

- **来源**：eval-doc #0b
- **优先级**：P0
- **前置条件**：protocol/*.py 已迁移到 zchat-protocol，channel-server/protocol/ 已删除
- **操作步骤**：
  1. `cd zchat-protocol && uv run pytest tests/ -v`
  2. `cd zchat-channel-server && uv run python -c "from zchat_protocol.conversation import Conversation; from zchat_protocol.gate import gate_message; print('OK')"`
  3. `uv run pytest tests/unit/ feishu_bridge/tests/ -q`
- **预期结果**：zchat-protocol 测试全通过；channel-server import 正常；238 unit passed
- **涉及模块**：zchat-protocol, channel-server 全局 import

### TC-002: R0 — gate_message 从 zchat_protocol 导入后行为不变

- **来源**：eval-doc #0a + #4
- **优先级**：P0
- **前置条件**：gate.py 在 zchat-protocol 中
- **操作步骤**：
  1. `from zchat_protocol.gate import gate_message`
  2. 调用 `gate_message(conv, Participant(id="op", role=OPERATOR), MessageVisibility.PUBLIC)` 在 copilot 模式
  3. 验证返回 `MessageVisibility.SIDE`
- **预期结果**：Gate 行为与迁移前完全一致
- **涉及模块**：zchat_protocol.gate

### TC-003: R0 — ConversationState 状态转换不变

- **来源**：eval-doc #0a
- **优先级**：P0
- **前置条件**：conversation.py 在 zchat-protocol 中
- **操作步骤**：
  1. `from zchat_protocol.conversation import Conversation, ConversationState, transition_state`
  2. created → active（合法）
  3. active → created（非法，应 raise ValueError）
- **预期结果**：状态机规则不变
- **涉及模块**：zchat_protocol.conversation

### TC-004: R1 — CommandHandler /hijack 独立执行

- **来源**：eval-doc #1
- **优先级**：P0
- **前置条件**：engine/command_handler.py 存在，从 server.py 提取
- **操作步骤**：
  1. 构造 CommandHandler(conv_manager, mode_manager, event_bus, bridge_server)
  2. `await handler.execute(Command("hijack"), conv_id, operator_id="op1")`
  3. 检查 conversation mode 变为 takeover
  4. 检查 bridge_server.send_event 被调用（mode.changed）
- **预期结果**：CommandHandler 可独立实例化和测试，不依赖 server.py
- **涉及模块**：engine/command_handler.py

### TC-005: R1 — CommandHandler /resolve 关闭 conversation

- **来源**：eval-doc #2
- **优先级**：P0
- **前置条件**：同 TC-004
- **操作步骤**：
  1. `await handler.execute(Command("resolve"), conv_id, operator_id="op1")`
  2. 检查 conversation state 变为 closed
  3. 检查 bridge_server.send_event("conversation.closed") 被调用
- **预期结果**：resolve 逻辑完全从 server.py 移出
- **涉及模块**：engine/command_handler.py

### TC-006: R1 — CommandHandler /status 查询

- **来源**：eval-doc #3
- **优先级**：P0
- **前置条件**：同 TC-004
- **操作步骤**：
  1. 创建 2 个 active conversation
  2. `result = await handler.execute(Command("status"))`
  3. 检查返回文本包含 "Active conversations: 2"
- **预期结果**：admin 命令逻辑可独立测试
- **涉及模块**：engine/command_handler.py

### TC-007: R2 — Gate 调用签名统一

- **来源**：eval-doc #4
- **优先级**：P0
- **前置条件**：所有 gate_message 调用使用 (Conversation, Participant, MessageVisibility) 签名
- **操作步骤**：
  1. `grep -rn "gate_message" server.py engine/ feishu_bridge/`
  2. 验证所有调用都用对象签名
  3. 运行 E2E test_gate_enforcement 和 test_mode_switching
- **预期结果**：无 string 调用残留；24 E2E passed
- **涉及模块**：server.py, engine/message_router.py

### TC-008: R3 — MessageRouter 统一处理 agent 回复

- **来源**：eval-doc #5
- **优先级**：P0
- **前置条件**：engine/message_router.py 存在
- **操作步骤**：
  1. 构造 MessageRouter(conv_manager, message_store, bridge_server)
  2. `await router.route_agent_message(nick="fast-agent", body="__msg:uuid:hello", conv_id="c1")`
  3. 检查 message_store.save 被调用
  4. 检查 bridge_server.send_reply 被调用（visibility 经过 Gate）
- **预期结果**：agent 消息路由可独立测试
- **涉及模块**：engine/message_router.py

### TC-009: R3 — MessageRouter customer 消息转发

- **来源**：eval-doc #6
- **优先级**：P0
- **前置条件**：同 TC-008
- **操作步骤**：
  1. `await router.route_customer_message(conv_id="c1", text="hello", sender="user1")`
  2. 检查 conversation activated
  3. 检查 irc_transport.privmsg 被调用（发给 agent nick）
- **预期结果**：customer 消息转发逻辑从 server.py 移出
- **涉及模块**：engine/message_router.py

### TC-010: R4 — VisibilityRouter 只做目标映射

- **来源**：eval-doc #7
- **优先级**：P1
- **前置条件**：visibility_router.py 不含 card 构建代码
- **操作步骤**：
  1. `router.route(conv_id, {"visibility": "public", "text": "hello"})`
  2. 检查 sender.send_text(customer_chat) 被调用
  3. 确认 _build_conv_card 不在 visibility_router.py 中
- **预期结果**：VisibilityRouter 无 Feishu card 逻辑
- **涉及模块**：feishu_bridge/visibility_router.py

### TC-011: R4 — FeishuRenderer card 构建独立

- **来源**：eval-doc #8
- **优先级**：P1
- **前置条件**：feishu_bridge/feishu_renderer.py 存在
- **操作步骤**：
  1. `card = renderer.build_conv_card(conv_id, metadata, mode="auto", state="active")`
  2. 验证 card JSON 结构正确（header + elements + action buttons）
- **预期结果**：card 构建是纯函数，可独立测试
- **涉及模块**：feishu_bridge/feishu_renderer.py

### TC-012: R7 — server.py 行数 < 200

- **来源**：eval-doc #12
- **优先级**：P1
- **前置条件**：R1-R6 完成
- **操作步骤**：
  1. `wc -l server.py`
  2. 检查 server.py 只含 build_components + wire callbacks + main
- **预期结果**：< 200 行
- **涉及模块**：server.py

### TC-013: 回归 — 全量 unit test

- **来源**：eval-doc #13
- **优先级**：P0
- **前置条件**：R0-R7 全部完成
- **操作步骤**：
  1. `uv run pytest tests/unit/ feishu_bridge/tests/ -q`
- **预期结果**：238+ passed, 0 failed, 0 skip
- **涉及模块**：全局

### TC-014: 回归 — 全量 E2E test

- **来源**：eval-doc #14
- **优先级**：P0
- **前置条件**：R0-R7 全部完成
- **操作步骤**：
  1. `uv run pytest tests/e2e/ -q --timeout=60`
- **预期结果**：24 passed, 0 failed
- **涉及模块**：全局

### TC-015: 新增 — CommandHandler unit tests

- **来源**：eval-doc #15
- **优先级**：P1
- **前置条件**：engine/command_handler.py 存在
- **操作步骤**：
  1. `uv run pytest tests/unit/test_command_handler.py -v`
- **预期结果**：10+ tests covering hijack/release/copilot/resolve/status/dispatch/review
- **涉及模块**：engine/command_handler.py

### TC-016: 新增 — MessageRouter unit tests

- **来源**：eval-doc #15
- **优先级**：P1
- **前置条件**：engine/message_router.py 存在
- **操作步骤**：
  1. `uv run pytest tests/unit/test_message_router.py -v`
- **预期结果**：8+ tests covering agent reply routing, customer message forwarding, Gate application
- **涉及模块**：engine/message_router.py

## 统计

| 指标 | 值 |
|------|-----|
| 总用例数 | 16 |
| P0 | 10 |
| P1 | 6 |
| P2 | 0 |
| 来源：eval-doc | 14 |
| 来源：regression | 2 |

## 风险标注

- **高风险**：R0（protocol 迁移）影响所有文件的 import，需要全量回归
- **高风险**：R2（Gate 签名统一）— server.py:420 的 string 调用可能有隐藏的兼容逻辑
- **回归风险**：R1（CommandHandler 提取）— E2E 测试通过 bridge callback 间接调用命令，callback 签名不能变
- **中风险**：R4（VisibilityRouter 分离）— card+thread 状态管理与 visibility 路由紧耦合
