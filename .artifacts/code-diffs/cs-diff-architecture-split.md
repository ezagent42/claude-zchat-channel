---
type: code-diff
id: cs-diff-architecture-split
status: confirmed
producer: skill-3
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-architecture-split
  - cs-plan-architecture-split
  - cs-report-architecture-split
---

# Code Diff: Task 4.6.1 — server.py 架构拆分

## 来源

- plan: `cs-plan-architecture-split`
- eval-doc: `cs-eval-architecture-split`
- 背景：`server.py`（644 行）承载了独立进程 + MCP server 双重职责。Phase 4.6.1 将其拆分为
  `server.py`（独立进程）+ `agent_mcp.py`（轻量 MCP 代理），实现职责分离和多实例扩展。

## 变更文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `server.py` | M | 去掉所有 MCP 相关代码（create_server / register_tools / inject_message / poll_irc_queue / load_instructions），保留 build_components + wire_bridge_callbacks + 独立进程入口 |
| `agent_mcp.py` | A | 新建轻量 MCP stdio 代理：4 个 tools (reply / join_channel / join_conversation / send_side_message) + IRC 连接 + @mention 注入 |
| `pyproject.toml` | M | 新增 entry_point `zchat-agent-mcp = "agent_mcp:entry_point"`；`[tool.hatch.build.targets.wheel]` only-include 增加 `agent_mcp.py` |
| `tests/unit/test_architecture_split.py` | A | 7 个 unit 测试验证模块职责边界 + entry_points |

## 改动类型

### server.py（Modified — 职责精简）

**去掉的代码**:
- `from mcp.server.lowlevel import Server` 等 MCP 导入
- `create_server()` — MCP Server 构造
- `register_tools()` — MCP tool 注册（reply / join / send_side_message）
- `inject_message()` — IRC 消息注入 MCP notification
- `poll_irc_queue()` — IRC 队列轮询
- `load_instructions()` — 加载 instructions.md 模板

**保留的代码**:
- `build_components()` — 组装所有 engine 组件（EventBus / ConversationManager / ModeManager / BridgeAPIServer / IRCTransport / TimerManager / MessageStore / ParticipantRegistry / SquadRegistry / PluginManager）
- `wire_bridge_callbacks()` — Bridge API 回调接线（命令处理、消息路由、事件分发）
- `entry_point()` / `main()` — 独立进程入口（IRC bot + Bridge API 启动）

### agent_mcp.py（Added — 新建文件，~336 行）

- 环境变量读取：`AGENT_NAME` / `IRC_SERVER` / `IRC_PORT` / `IRC_CHANNELS` / `IRC_TLS` / `IRC_AUTH_TOKEN`
- `inject_message()` — 将 IRC 消息以 `notifications/claude/channel` MCP notification 注入 Claude Code
- `poll_irc_queue()` — 持续从 asyncio.Queue 读取 IRC 消息并注入
- `load_instructions()` — 加载 `instructions.md` 模板并替换 `$agent_name`
- `create_server()` — 构造 `mcp.server.lowlevel.Server` 实例
- `register_tools()` — 注册 4 个 MCP tools:
  - `reply` — 回复用户/频道，支持 edit_of 编辑和 side channel
  - `join_channel` — 加入 IRC 频道
  - `join_conversation` — 加入对话频道 (#conv-{id})
  - `send_side_message` — 发送 side-channel 消息（operator+admin only）
- `main()` — MCP stdio + IRC 连接 + anyio task group 并发运行
- `entry_point()` — `asyncio.run(main())` 入口

### pyproject.toml（Modified）

```toml
[project.scripts]
zchat-channel = "server:entry_point"
zchat-agent-mcp = "agent_mcp:entry_point"   # 新增

[tool.hatch.build.targets.wheel]
only-include = [
    "server.py", "agent_mcp.py", ...         # 新增 agent_mcp.py
]
```

## 影响模块

- `server.py` — 职责精简，仅独立进程
- `agent_mcp.py` — 新增，MCP 代理
- `pyproject.toml` — entry_points + build 配置
- 测试套件 — 新增 7 个 unit 测试

**零改动模块**：engine/ protocol/ bridge_api/ transport/ message.py instructions.md

## 风险评估

- **低风险**：纯代码组织重构，不修改任何 engine 行为逻辑。
- engine/ protocol/ bridge_api/ transport/ 完全未动。
- 原有 tests 无需修改即可继续 PASS。
- agent_mcp.py 的 tool 实现从 server.py 直接提取，逻辑等价。
