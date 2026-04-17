# zchat-channel-server

Claude Code Channel plugin — bridges IRC messaging and Claude Code via MCP.

## Install

```bash
claude plugin install zchat-channel
```

## Usage

```bash
# Start Claude Code with the channel plugin
claude --dangerously-load-development-channels plugin:zchat-channel

# Agent joins IRC server as "agent0" (configurable via AGENT_NAME env var)
# Any IRC client can interact with the agent
```

## Environment Variables

- `AGENT_NAME` — agent identifier (default: `agent0`)
- `IRC_SERVER` — IRC server address (default: 127.0.0.1)
- `IRC_PORT` — IRC server port (default: 6667)
- `CS_ROUTING_CONFIG` — routing config file path（默认：与 `server.py` 同目录的 `routing.toml`）
- `CS_DB_PATH` — SQLite 数据库路径（默认：`conversations.db`）
- `BRIDGE_HOST` / `BRIDGE_PORT` — Bridge API 监听地址（默认：`127.0.0.1:9999`）

## Routing Configuration

channel-server 启动时加载 `routing.toml`，控制 conversation 的 agent dispatch 行为。

### 文件位置

默认从 **server.py 同目录的 `routing.toml`** 加载。可通过环境变量覆盖：

```bash
CS_ROUTING_CONFIG=/etc/zchat/routing.toml python server.py
```

文件不存在时不报错，所有字段使用空默认值。

### 字段说明

```toml
[routing]

# 新 conversation 自动 dispatch 的 agent 列表
# 空列表 = 不自动 dispatch，需 operator 手动 /dispatch
default_agents = ["alice-agent0"]

# 升级链 — conversation 超时或主动升级时按顺序尝试 dispatch
# 空列表 = 不自动升级
# "operator" 为特殊值，表示升级到人工运营席位
escalation_chain = ["alice-senior", "operator"]

# /dispatch 命令白名单
# 空列表（默认）= 不限制，operator 可 dispatch 到任意 agent
# 非空时只允许 dispatch 到列表中的 agent
available_agents = ["alice-agent0", "alice-senior"]
```

### 默认行为（无配置文件）

| 字段 | 默认值 | 效果 |
|------|--------|------|
| `default_agents` | `[]` | 新 conversation 不自动 dispatch |
| `escalation_chain` | `[]` | SLA 超时仅记录事件，不自动升级 |
| `available_agents` | `[]` | /dispatch 无白名单限制 |

### 与 v3 架构的关系

在 v3 架构中，每个 IRC channel 对应一个 conversation（per-conversation 模型）。
`routing.toml` 定义的是**全局默认**路由策略：

- `default_agents` 决定新 conversation 的**首发** agent（v3 §6 Agent 编排）
- `escalation_chain` 实现**多级升级**（tier-1 bot → tier-2 bot → 人工）
- `available_agents` 白名单保护生产环境不被误 dispatch 到测试 agent

参考模板：[routing.example.toml](./routing.example.toml)
