---
id: cs-eval-server
type: eval-doc
status: confirmed
phase: "Phase 4: Transport + Server v1.0 Integration"
created_at: "2026-04-14"
related_ids:
  - cs-plan-server
---

# cs-eval-server — IRC Transport 提取 + server.py v1.0 集成重构

## 特性描述

在 Phase 2 (engine/) + Phase 3 (bridge_api/) 都已合并的基础上，对 `zchat-channel-server`
做最后一步集成重构：

1. 把 `server.py` 中 IRC 连接/事件处理逻辑（L76–L180 `setup_irc` + `on_pubmsg` /
   `on_privmsg` / `on_welcome` / `on_disconnect` / `_handle_sys_message`）提取到
   `transport/irc_transport.py` 的 `IRCTransport` 类。
2. `server.py` 变为胶水代码：组装 `EventBus` / `ConversationManager` /
   `ModeManager` / `TimerManager` / `ParticipantRegistry` / `MessageStore` /
   `BridgeAPIServer` / `IRCTransport` 并启动。
3. 引入可运行的 E2E 测试：通过 ergo + pytest fixture 起真 IRC server 并驱动
   channel-server 子进程，验证 Bridge 注册 / customer_connect 行为。

## 期望行为

| 能力 | 期望 |
|------|------|
| `IRCTransport` 构造 | 接收 `server` / `port` / `nick` / `tls` / `auth_token`，初始化 `joined_channels=set()` |
| `IRCTransport.conv_channel_name` | `"feishu_oc_abc" → "#conv-feishu_oc_abc"` |
| `IRCTransport.extract_conv_id` | `"#conv-feishu_oc_abc" → "feishu_oc_abc"`；非对话频道返回 `None` |
| 现有 12 个 unit 测试 | 无回归（实际现状 102 个，全部保持 PASS） |
| `server.py` 入口 | `python -m server`（或 `zchat-channel`）在设置好 env 后可直接启动 |
| Bridge 注册 E2E | WebSocket `register` 后收到 `registered` 回执 |
| Bridge customer_connect E2E | 经 `BridgeAPIServer._handle_customer_connect` 在 `ConversationManager` 创建新对话 |
| 不依赖的 SKIP | E2E 必须 0 SKIP；如 ergo 缺失则 fixture error，不用 skip 粉饰 |

## 设计约束

- `IRCTransport` 不引入 engine/bridge_api 依赖，只做 IRC 侧连接 + 队列投递（注入函数/回调）
- `server.py` main() 继续保持 MCP stdio 主流程；Bridge WebSocket 通过
  `asyncio.create_task` 并行启动
- 保留两个已稳定的 MCP tool: `reply`, `join_channel`（以 IRC 为主干的原 0.2.0
  行为）；新的 5 个 conversation-aware MCP tools（`edit_message` / `join_conversation`
  / `send_side_message` / `list_conversations` / `get_conversation_status`）只做
  **骨架注册 + 基础 happy path**，详细 gate/participant/plugin 等业务会在 Phase 5 CLI
  或后续 Phase 中集成 —— 本 Phase 不承诺完整业务链路，只承诺：
  - tool 已注册（`handle_list_tools` 会返回 7 个工具）
  - happy path 不抛异常（通过 unit mock 覆盖）
- `ConversationManager.create()` 签名为 `(conversation_id, metadata=None)`，与
  `bridge_api._handle_customer_connect(msg["customer"])` 存在参数不匹配
  —— **在 Phase 4 内修复**：`_handle_customer_connect` 只传
  `conversation_id` + `metadata={"customer": msg["customer"], **msg.get("metadata", {})}`
- E2E 不 mock 任何组件；实在启动不起来就让测试 ERROR，绝不 SKIP

## 非目标（Out of Scope）

- 完整 gate_engine + message_store + plugin_manager 的集成路径（放 Phase 5 / Phase 6）
- Feishu bridge 的 adapter（Phase 4.5）
- Admin / Squad 层实际命令执行（hijack / handback / dispatch / assign）
- `send_reply` 的真实 IRC 侧广播

## Spec 参考

- `spec/channel-server/02-channel-server.md` §4 MCP Tools
- `spec/channel-server/02-channel-server.md` §6 IRC Transport
- `spec/channel-server/02-channel-server.md` §7 启动流程
- `docs/discuss/plan/05-phase4-integration.md`

## 验收

- `.artifacts/e2e-reports/cs-report-server.md` 存在，0 FAIL 0 SKIP
- `.artifacts/registry.json` 有 4 条 `cs-*-server` 记录，链条闭合
- `feat/server-v1` 已 push 到 origin
