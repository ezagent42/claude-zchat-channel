---
type: coverage-matrix
id: cs-coverage-matrix-001
status: draft
producer: skill-0
created_at: "2026-04-14"
---

# Coverage Matrix: zchat-channel-server

## 概览

| 指标 | 值 |
|------|-----|
| 总模块数 | 2 |
| 代码测试覆盖模块 | 2/2 |
| 操作 E2E 覆盖流程 | 0/8 |
| 环境受限测试 | 0 (unit tests 无环境依赖) |

## 代码测试覆盖

| 模块 | 测试文件 | 测试命令 | 结果 | 覆盖状态 |
|------|---------|---------|------|---------|
| cs_server | tests/unit/test_legacy.py | `uv run pytest tests/unit/test_legacy.py -v` | 5/5 passed | ✅ covered |
| cs_message | tests/unit/test_message.py | `uv run pytest tests/unit/test_message.py -v` | 7/7 passed | ✅ covered |

### 覆盖细节

**cs_server** (5 tests):
- sys message 编解码 roundtrip (test_legacy.py:8)
- 普通文本不被误识别为 sys message (test_legacy.py:16)
- instructions.md $agent_name 插值 (test_legacy.py:21)
- instructions.md 包含路由规则 (test_legacy.py:27)
- instructions.md 包含 soul.md 引用 (test_legacy.py:37)

**cs_message** (7 tests):
- @mention 检测 (test_message.py:7)
- @mention 清理 (test_message.py:13)
- 短消息单 chunk (test_message.py:17)
- 长 ASCII 分 chunk (test_message.py:21)
- CJK 字符分 chunk (test_message.py:28)
- 换行替换为空格 (test_message.py:36)
- dash 分隔符 agent name 检测 (test_message.py:45)

### 未覆盖的代码路径

| 函数/方法 | 位置 | 说明 |
|-----------|------|------|
| `inject_message()` | server.py:43 | 需要 MCP write_stream mock |
| `poll_irc_queue()` | server.py:63 | async loop，需要 asyncio 测试 |
| `setup_irc()` | server.py:76 | 需要 IRC server 或 mock reactor |
| `on_welcome/pubmsg/privmsg/disconnect` | server.py:97-156 | IRC 事件处理器，嵌套函数 |
| `_handle_sys_message()` | server.py:186 | 部分通过 roundtrip test 间接覆盖 |
| `create_server()` | server.py:219 | 需要 MCP Server mock |
| `register_tools()` | server.py:224 | 需要 Server + state dict mock |
| `_handle_reply()` | server.py:270 | 需要 IRC connection mock |
| `_handle_join_channel()` | server.py:280 | 需要 IRC connection mock |
| `main()` | server.py:290 | 集成入口，需要 E2E 环境 |

## 操作 E2E 覆盖

| 用户流程 | E2E 测试 | 证据类型 | 覆盖状态 |
|---------|---------|---------|---------|
| MCP server 启动并连接 IRC | (无) | - | ❌ not covered |
| Channel @mention → Claude 通知 | (无) | - | ❌ not covered |
| Private message → Claude 通知 | (无) | - | ❌ not covered |
| 系统消息处理 (stop/join/status) | (无) | - | ❌ not covered |
| MCP tool: reply (发送 IRC 消息) | (无) | - | ❌ not covered |
| MCP tool: join_channel | (无) | - | ❌ not covered |
| /zchat:broadcast 广播消息 | (无) | - | ❌ not covered |
| /zchat:dm 私聊消息 | (无) | - | ❌ not covered |

## 环境受限覆盖

| 测试 | 所需环境 | 状态 | 说明 |
|------|---------|------|------|
| (无 E2E 测试文件) | ergo IRC server + MCP stdio | — | E2E 目录为空 (tests/e2e/__init__.py only) |

## E2E 缺口清单

按优先级排序，这是 Skill 2 (test-plan-generator) 的第一批输入：

1. **MCP server 启动** — 启动 zchat-channel 进程，验证 IRC 连接建立、channel join 成功
2. **Channel @mention 通知** — 在 IRC channel 发送 `@agent0 hello`，验证 Claude Code 收到 MCP notification
3. **Private message 通知** — 通过 IRC PRIVMSG 发送消息给 agent nick，验证 Claude Code 收到通知
4. **MCP reply tool** — 通过 MCP 调用 reply tool，验证 IRC channel 收到消息（含 chunk 分片）
5. **系统消息 stop** — 发送 sys.stop_request，验证收到 sys.stop_confirmed
6. **系统消息 join** — 发送 sys.join_request，验证 agent 加入指定 channel
7. **系统消息 status** — 发送 sys.status_request，验证返回 channel 列表和计数器
8. **MCP join_channel tool** — 通过 MCP 调用 join_channel，验证 IRC JOIN 成功
