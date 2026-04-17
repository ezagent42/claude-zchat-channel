# zchat-channel-server

IRC ↔ WebSocket 消息路由 + 插件命令处理。

## 架构

```
Bridge (WS) ←→ Router ←→ IRC (ergo)
                 ↓
            PluginRegistry
           (mode/sla/resolve)
```

- **Router** — WS↔IRC 双向翻译 + "/" 命令分派
- **RoutingTable** — channel→agents 映射（routing.toml）
- **Plugin** — 命令扩展（mode/sla/resolve）
- **WSServer** — Bridge WebSocket 接入
- **IRCConnection** — IRC 传输

每个 Agent 运行独立的 `agent_mcp.py` 进程，提供 MCP tools（reply/run_zchat_cli）。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `IRC_SERVER` | `127.0.0.1` | IRC server 地址 |
| `IRC_PORT` | `6667` | IRC server 端口 |
| `CS_NICK` | `cs-bot` | channel-server 的 IRC nick |
| `IRC_TLS` | `false` | 是否使用 TLS |
| `IRC_PASSWORD` | (空) | IRC 密码 |
| `WS_HOST` | `127.0.0.1` | Bridge WebSocket 监听地址 |
| `WS_PORT` | `9999` | Bridge WebSocket 监听端口 |
| `CS_ROUTING_CONFIG` | `routing.toml` | 路由配置文件路径 |

Agent MCP 进程额外使用：`AGENT_NAME`, `IRC_CHANNELS`, `IRC_AUTH_TOKEN`。

## Routing 配置

```toml
[channels.general]
agents = { primary = "alice-agent0" }

[channels."conv-001"]
external_chat_id = "oc_xxx"    # Bridge 用来找外部群（可选）
agents = { fast = "alice-fast0", deep = "alice-deep0" }
```

- `agents` — role→nick 映射，Router 用来决定 @ 谁
- `external_chat_id` — Bridge 用来关联外部系统的群 ID

文件不存在时使用空路由表，不报错。

参考模板：[routing.example.toml](./routing.example.toml)

## Plugin 命令

| 命令 | Plugin | 说明 |
|------|--------|------|
| `/hijack` | mode | 切换到 takeover 模式（operator 接管） |
| `/release` | mode | 切回 copilot 模式（agent 驱动） |
| `/copilot` | mode | 同 /release |
| `/resolve` | resolve | 标记对话结束（emit channel_resolved 事件） |

SLA plugin 无命令，监听 mode_changed 事件，takeover 超时后自动触发 /release。

## MCP Tools（Agent 可用）

| Tool | 说明 |
|------|------|
| `reply` | 发消息/编辑/side/命令 |
| `run_zchat_cli` | 执行 zchat CLI 命令 |

## 开发

```bash
# 运行测试
uv run pytest tests/unit/ -v

# 启动 channel-server
uv run python -m channel_server
```
