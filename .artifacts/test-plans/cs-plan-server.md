---
id: cs-plan-server
type: test-plan
status: draft
phase: "Phase 4: Transport + Server v1.0 Integration"
created_at: "2026-04-14"
related_ids:
  - cs-eval-server
  - cs-diff-server
---

# cs-plan-server — Phase 4 测试计划（Unit + E2E）

## 测试文件清单

| 文件 | 层级 | 测试数 | 覆盖模块 |
|------|------|--------|----------|
| `tests/unit/test_irc_transport.py` | unit | 7 | `transport/irc_transport.py` |
| `tests/unit/test_server_integration.py` | unit | 4 | `server.py` 组装路径（mock IRC） |
| `tests/e2e/conftest.py` | e2e fixture | — | ergo + channel-server 子进程 |
| `tests/e2e/test_bridge_registration.py` | e2e | 2 | Bridge 注册回执 |
| `tests/e2e/test_customer_connect.py` | e2e | 1 | customer_connect → ConversationManager |
| `tests/e2e/test_server_startup.py` | e2e | 1 | server 子进程能启动、保持存活 |

**总计**: 11 unit + 4 E2E = **15 新增**；叠加现有 **102 unit 测试**，合计 **117 条**。

## Unit 测试用例

### `tests/unit/test_irc_transport.py`

| TC-ID | 用例 | 源 |
|-------|------|----|
| TC-U01 | `test_init_defaults` — 构造后 `nick/server/port` 存在，`joined_channels=set()` | plan §Task 4.1 |
| TC-U02 | `test_conv_channel_name` — `conv_channel_name("feishu_oc_abc") == "#conv-feishu_oc_abc"` | plan §Task 4.1 |
| TC-U03 | `test_extract_conv_id_valid` — `extract_conv_id("#conv-abc") == "abc"` | plan §Task 4.1 |
| TC-U04 | `test_extract_conv_id_invalid` — `extract_conv_id("#admin") is None` | plan §Task 4.1 |
| TC-U05 | `test_extract_conv_id_no_hash` — `extract_conv_id("abc") is None` | 边界 |
| TC-U06 | `test_sys_stop_request_reply` — `_handle_sys_message({"type":"sys.stop_request",...})` 回 `sys.stop_confirmed` | eval §IRCTransport |
| TC-U07 | `test_sys_join_request_joins_channel` — `sys.join_request` 触发 `connection.join("#...")` 并加入 `joined_channels` | eval §IRCTransport |

### `tests/unit/test_server_integration.py`

| TC-ID | 用例 | 源 |
|-------|------|----|
| TC-U08 | `test_register_tools_lists_seven_tools` — `handle_list_tools()` 返回 7 个工具（2 原 + 5 新） | eval §MCP tools |
| TC-U09 | `test_main_builds_components` — 通过导入 `server` 能组装 `EventBus/ConversationManager/ModeManager/TimerManager/ParticipantRegistry/BridgeAPIServer/IRCTransport`（mock IRC）不抛异常 | eval §组装 |
| TC-U10 | `test_bridge_customer_connect_creates_conversation` — `BridgeAPIServer._handle_customer_connect` 调用修正后的 `ConversationManager.create(conversation_id, metadata={...})` | eval §ConversationManager 兼容 |
| TC-U11 | `test_list_conversations_tool_happy_path` — `list_conversations` tool 调用返回 TextContent 列表 | eval §MCP tools skeleton |

## E2E 测试用例

ergo 必须可用（`which ergo` 已验证 `/home/linuxbrew/.linuxbrew/bin/ergo-2.18.0`）。
`conftest.py` 负责：

1. `ergo_server` fixture: 写 `ergo.yaml`，`subprocess.Popen(["ergo", "run", ...])`，启动后等 IRC 端口可连。
2. `channel_server` fixture: `subprocess.Popen(["uv", "run", "python", "-m", "server"], env=...)`，等 `BRIDGE_PORT` WebSocket 可连。
3. `bridge_ws` fixture: `websockets.connect(...)` 后发 register，收到 `registered` 后 yield。

| TC-ID | 文件 | 用例 | 期望 |
|-------|------|------|------|
| TC-E01 | `test_bridge_registration.py` | `test_register_returns_registered_ack` | 发 `register` 后收到 `{"type":"registered"}` 含 `instance_id` |
| TC-E02 | `test_bridge_registration.py` | `test_register_capabilities_preserved` | 注册后通过第二个 customer WS 发 customer_connect，不会被 "unhandled" 忽略 |
| TC-E03 | `test_customer_connect.py` | `test_customer_connect_creates_conversation` | 发 `customer_connect` 后，通过 `get_conversation_status` MCP 协议 不可用（因 stdio 不暴露），改为直接检查 channel-server 输出 `sqlite` 文件：conversation row 出现 |
| TC-E04 | `test_server_startup.py` | `test_server_subprocess_alive` | 启动 3s 后进程 `poll() is None`（没崩溃） |

**执行方式**：

```bash
cd zchat-channel-server
uv run pytest tests/unit/ -v                         # 应 PASS 113 条（102 + 11）
uv run pytest tests/e2e/ -v -m e2e --timeout=30      # 应 PASS 4 条
```

## 覆盖缺口（明确延迟的部分，不 skip）

- `on_pubmsg` / `on_privmsg` 真实 IRC 事件触发 → 放 Phase 5 CLI E2E（需真人类模拟 IRC 客户端）
- `send_reply` visibility 路由的实际广播 → 放 Phase 4.5 (feishu bridge)
- `edit_message` / `join_conversation` / `send_side_message` / `get_conversation_status` 的真实业务路径 → 只验证 happy path 骨架调用

## 退出标准

- 117 条测试 0 FAIL 0 SKIP
- `.artifacts/e2e-reports/cs-report-server.md` 落地
