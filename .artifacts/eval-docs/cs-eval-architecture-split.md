---
type: eval-doc
id: cs-eval-architecture-split
status: confirmed
mode: verify
feature: "channel-server 独立化 — server.py 拆分为独立进程 + agent_mcp.py"
producer: skill-5
submitter: yaosh
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-plan-architecture-split
  - cs-diff-architecture-split
  - cs-report-architecture-split
---

# Eval: channel-server 独立化 — server.py 拆分

## 背景

Phase 4 完成后 `server.py` 承载了两重职责：
1. **独立进程**：IRC bot (cs-bot) + Bridge API :9999 + engine 组装（ConversationManager / EventBus / ModeManager 等）
2. **MCP stdio 服务**：Claude Code agent 的 MCP tools (reply / join / send_side_message) + IRC @mention 注入

两个职责耦合在同一文件（644 行），导致：
- 每个 agent 进程都启动完整 engine，浪费资源
- 无法独立扩展 agent 数量（server 只需一个，agent_mcp 可多实例）
- MCP 框架依赖（`mcp.server.stdio`）与 engine 模块交叉引用

需求来源：`docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.1。

## Before / After 架构

### Before（Phase 4）

```
Claude Code agent
  └─ MCP stdio ─→ server.py (644 行)
                   ├─ MCP Server (create_server / register_tools / inject_message)
                   ├─ IRC 连接
                   ├─ Engine 组装 (build_components)
                   ├─ Bridge callbacks (wire_bridge_callbacks)
                   └─ Bridge API :9999
```

每个 agent 进程 = 完整 server.py = 完整 engine + IRC + Bridge。

### After（Phase 4.6.1）

```
                          ┌─────────────────────────────────┐
                          │ server.py — 独立进程 (cs-bot)     │
                          │  ├─ build_components()           │
                          │  ├─ wire_bridge_callbacks()      │
                          │  ├─ IRC bot (IRCTransport)       │
                          │  └─ Bridge API :9999             │
                          └─────────────────────────────────┘
                                         ▲
            ┌────────────────────────────┤ (Bridge API / IRC)
            ▼                            ▼
┌─────────────────────┐    ┌─────────────────────┐
│ agent_mcp.py (inst1)│    │ agent_mcp.py (inst2)│
│  ├─ MCP stdio       │    │  ├─ MCP stdio       │
│  ├─ 4 tools         │    │  ├─ 4 tools         │
│  ├─ IRC 连接        │    │  ├─ IRC 连接        │
│  └─ @mention 注入   │    │  └─ @mention 注入   │
└─────────────────────┘    └─────────────────────┘
```

- `server.py`：中心进程，持有所有 engine 组件，不包含 MCP 代码
- `agent_mcp.py`：轻量 MCP 代理，每个 Claude Code agent 一个实例，只做 IRC 通信 + MCP tool 转发

## 行为预期

| # | 预期 | 状态 |
|---|------|------|
| 1 | `server.py` 不包含 `create_server` / `register_tools` / `inject_message` / `poll_irc_queue` / `load_instructions` | CONFIRMED |
| 2 | `server.py` 保留 `build_components` / `wire_bridge_callbacks` / `entry_point` / `main` | CONFIRMED |
| 3 | `agent_mcp.py` 包含 `create_server` / `register_tools` / `inject_message` / `poll_irc_queue` / `load_instructions` / `entry_point` | CONFIRMED |
| 4 | `agent_mcp.py` 不包含 `build_components` / `wire_bridge_callbacks`（不持有 engine 组件） | CONFIRMED |
| 5 | `agent_mcp.py` 注册 4 个 MCP tools: reply, join_channel, join_conversation, send_side_message | CONFIRMED |
| 6 | `pyproject.toml` 有两个 entry_points: `zchat-channel` 和 `zchat-agent-mcp` | CONFIRMED |
| 7 | engine/ protocol/ bridge_api/ transport/ 0 改动 | CONFIRMED |
| 8 | 原有 tests 全部 PASS（无回归） | CONFIRMED |

## 风险

- **无重大风险**：拆分是纯代码组织变更，不修改 engine 行为。
- 原有 E2E conftest 的 `channel_server` fixture 需适配为 `subprocess.Popen(["uv", "run", "zchat-channel"])`。

## 验证范围

- 覆盖：7 个 unit 测试（test_architecture_split.py），验证模块职责边界 + entry_points。
- 不覆盖：E2E 进程通信（属于后续 Task 4.6.2+ 的验证范围）。
