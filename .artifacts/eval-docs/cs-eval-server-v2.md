---
id: cs-eval-server-v2
type: eval-doc
status: confirmed
mode: simulate
phase: "Phase 4 补全: mode switching + gate enforcement E2E"
created_at: "2026-04-15"
related_ids:
  - cs-eval-server
  - cs-plan-server-v2
---

# cs-eval-server-v2 — Phase 4 补全 E2E 评估

## Feature 描述

Phase 4 首次提交（cs-eval-server）建立了 Bridge 注册 / customer_connect / 子进程存活的 E2E
基础，但缺少两类验证：

1. **Mode Switching**：当 Operator 通过 Bridge 发送 `operator_join`，channel-server 应触发
   `auto → copilot` 模式切换，并将 `mode.changed` 事件广播回所有 Bridge 连接；
   当 Operator 发送 `/hijack` 命令，应触发 `copilot → takeover`，同样广播事件。

2. **Gate Enforcement**：在 `takeover` 模式下，visibility = `side` 的消息（gate 对 agent
   public 消息的降级结果）应只路由到 `operator`/`admin` capability 的 Bridge 连接，
   不应到达 `customer` capability 的连接。

## 代码现状分析（模拟基础）

| 组件 | 现状 | 差距 |
|------|------|------|
| `bridge_api._handle_connection` | 处理 `register / customer_connect / customer_message / operator_message / operator_command / admin_command` | **缺 `operator_join`** |
| `BridgeAPIServer.on_operator_command` | 回调槽已声明（L67），`_handle_connection` L190 会调用 | **server.py 未注入实现** |
| `BridgeAPIServer.send_reply()` | visibility 路由已实现（L136–162），按 `compute_visibility_targets` 分发 | 可直接复用，只需调用 |
| `BridgeAPIServer.send_event()` | **不存在** | 需新增：广播 `{type:"event", event_type:..., data:..., conversation_id:...}` 到所有连接 |
| `ModeManager.atransition()` | 已实现，发出 EventBus 事件 | 需在 callback 中调用并将结果广播到 Bridge |
| `ConversationManager.add_participant()` | 已实现，有并发限制检查 | operator_join 中需创建 Participant + 调用 |
| `gate_message()` | 纯函数，已验证通过 40 unit 测试 | E2E 需借助 send_reply visibility 间接验证 |
| `ParticipantRegistry` | 已实现，识别 nick → Participant | operator_join 中注册 operator |

## Testcase 列表

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 模拟效果 | 差异/风险 | 优先级 |
|---|------|---------|---------|---------|---------|----------|-------|
| TC-M01 | operator_join 触发 auto→copilot | conversation 已创建（auto 模式），ergo+server 运行 | bridge_ws 发 `customer_connect`；发 `operator_join` | Bridge 收到 `{type:"event", event_type:"mode.changed", data:{from:"auto",to:"copilot"}}` | **不可行（现状）**：`_handle_connection` 无 `operator_join` 分支，消息被 `logger.debug("unhandled")` 丢弃 | 需新增 handler + `send_event()` 方法 | P0 |
| TC-M02 | /hijack 触发 copilot→takeover | conversation 在 copilot 模式（TC-M01 已执行） | bridge_ws 发 `operator_command {command:"/hijack"}` | Bridge 收到 `mode.changed, to:"takeover"` | **不可行（现状）**：`on_operator_command` 槽存在但 server.py 未注入，L193 条件 `and self.on_operator_command` 为 False，走 `logger.debug` | 需在 server.py main() 中注入 `/hijack` → `mode_manager.atransition` 实现 | P0 |
| TC-G01 | side 消息不到 customer bridge | 两个 WS：customer bridge（caps=["customer"]）、operator bridge（caps=["operator"]）；conversation 在 takeover 模式 | server 内部调用 `send_reply(conv_id, "...", "side")` | operator bridge 收到消息；customer bridge 不收到（timeout） | **可行（现状）**：`send_reply` + `compute_visibility_targets` 已正确实现；需要触发路径（借助 /hijack 后的系统通知） | 需设计 E2E 触发路径：`/hijack` callback 发 side 系统通知 | P0 |
| TC-G02 | public 消息到所有 bridge | 两个 WS；conversation 在 copilot 模式 | 触发 `send_reply(conv_id, "...", "public")` | customer + operator 都收到 | **可行（现状）**：路由表已正确 | 需要可触发的路径（operator_join 确认消息） | P1 |
| TC-M03 | operator_join 边界：conversation 不存在 | ergo+server 运行，无该 conversation | 发 `operator_join {conversation_id: "not_exist"}` | Bridge 连接保持，不崩溃，服务端 log 一条 warning | **未覆盖**：ConversationManager.get 返回 None，需要 server 回调中判断 | 需防御性检查 | P1 |

## 实现范围（本次补全）

| 组件 | 改动 | 测试覆盖 |
|------|------|---------|
| `bridge_api/ws_server.py` | 新增 `on_operator_join` 回调槽 + handler；新增 `send_event()` 方法 | `test_mode_switching.py` E2E |
| `server.py` | 注入 `bridge_server.on_operator_join` + `bridge_server.on_operator_command`（/hijack /release /copilot）；callback 调 `mode_manager.atransition` + `bridge_server.send_event` | E2E 全流程 |
| 新增 E2E 测试 | `tests/e2e/test_mode_switching.py`（2 条）+ `tests/e2e/test_gate_enforcement.py`（2 条） | — |

## 设计约束

- `send_event()` 广播到**所有**已注册 Bridge 连接（无 visibility 过滤），mode 状态变化是协议级事件
- Gate enforcement E2E 不直接 mock IRC；通过两个 Bridge WS 的差异接收来验证 `compute_visibility_targets` + `send_reply` 联动
- 所有 E2E 用真实 ergo + 真实 channel-server 子进程；0 mock
- 本次不承诺 `operator_message` / `customer_message` 的完整 gate 链路（那需要 MCP + IRC）

## 非目标

- IRC 侧 on_pubmsg / on_privmsg 的 gate 集成（Phase 5）
- admin_command 的完整路由（Phase 5+）

## 验收标准

- `uv run pytest tests/e2e/ -v -m e2e --timeout=30` → 全 PASS（≥ 8 条，0 SKIP）
- `cs-plan-server-v2` / `cs-diff-server-v2` / `cs-report-server-v2` 链条闭合

## Spec 参考

- `spec/channel-server/02-channel-server.md` §5 Bridge API
- `docs/discuss/plan/05-phase4-integration.md` TC-E02 / TC-E03
