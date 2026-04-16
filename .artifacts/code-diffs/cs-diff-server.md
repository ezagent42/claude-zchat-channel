---
id: cs-diff-server
type: code-diff
status: implemented
phase: "Phase 4: Transport + Server v1.0 Integration"
created_at: "2026-04-14"
related_ids:
  - cs-eval-server
  - cs-plan-server
---

# cs-diff-server — Phase 4 实现差异

> 分支: `feat/server-v1` @ `zchat-channel-server` submodule
> 基线: `feat/engine` (098f2de, Phase 2+3 merged)

## 文件改动

### 新增

| 文件 | 行数 | 说明 |
|------|------|------|
| `transport/__init__.py` | 3 | 包声明 + re-export IRCTransport |
| `transport/irc_transport.py` | ~210 | `IRCTransport` 类，从旧 `server.setup_irc` 提取 |
| `tests/unit/test_irc_transport.py` | ~65 | 7 条 unit 测试 |
| `tests/unit/test_server_integration.py` | ~110 | 4 条组装/tool 测试 |
| `tests/e2e/conftest.py` | ~200 | ergo + channel-server + bridge_ws fixture |
| `tests/e2e/test_bridge_registration.py` | ~40 | 2 条 E2E |
| `tests/e2e/test_customer_connect.py` | ~55 | 1 条 E2E |
| `tests/e2e/test_server_startup.py` | ~18 | 1 条 E2E |

### 修改

| 文件 | 说明 |
|------|------|
| `server.py` | 从 260 行重写为胶水层（~375 行含 7 个 tool schema），IRC 连接下沉到 `IRCTransport` |
| `bridge_api/ws_server.py` | `_handle_customer_connect`：把 `customer` 塞进 metadata，避免 `ConversationManager.create` 参数不兼容 |

## 关键设计

1. **依赖方向**：`transport/` 仅依赖 `zchat_protocol.sys_messages` 和 `irc` 库；不反向依赖 `engine/` / `bridge_api/`。
2. **回调注入**：`IRCTransport.start(queue, loop, on_pubmsg=..., on_privmsg_text=...)` 以回调方式接收事件，业务逻辑集中在 `server._on_pubmsg` / `server._on_privmsg`。
3. **启动顺序**：`build_components()` → `bridge_server.start()` → `stdio_server` ctx → `irc_transport.start()`。这样 Bridge 在 MCP stdio 起身前就绪，便于 E2E 独立测试。
4. **stdin 挂起**：MCP stdio 在 E2E 子进程中永远读不到数据，但 Bridge 子任务独立运行；通过 `stdin=PIPE` 让子进程不会因 EOF 关闭。

## 完成标准校验

- [x] `transport/irc_transport.py` 存在；`server.py` 不再保留 IRC reactor 细节
- [x] 新增 5 个 MCP tool 已注册（骨架 happy path 测试通过）
- [x] 现有 102 条 unit 测试无回归
- [x] 4 条 E2E 全 PASS（0 SKIP）
- [x] `bridge_api` 与 `ConversationManager` 参数契约对齐
