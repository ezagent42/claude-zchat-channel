# zchat-channel-server Bootstrap Report

**Generated**: 2026-04-17  
**Version**: 1.0.0.dev0  
**Python**: 3.13.5 / uv 0.7.15  

## 1. 项目概览

MCP server 桥接 IRC 和 Claude Code。V4 重构 + 清理后的精简架构：

- **Core infrastructure** (`src/channel_server/`): 纯路由 + 插件框架 + WS server + IRC 连接
- **Business plugins** (`src/plugins/`): 仅 mode + sla 两个插件（audit/lifecycle/activation 已删除）
- **Agent MCP** (`agent_mcp.py`): 每个 agent 运行的 MCP stdio 代理（已移除 fcntl lock）
- **Feishu bridge** (`src/feishu_bridge/`): 飞书 ↔ channel-server 协议转换（可选）

### 数据流

```
飞书/WeeChat → Bridge API (WS) → Router → IRC → Agent (MCP)
                                    ↓
                              PluginRegistry
                              (mode / sla)
```

### 与上次 bootstrap 的主要差异

| 变更 | 说明 |
|------|------|
| 删除 audit plugin | `src/plugins/audit/` 目录 + `tests/unit_v4/test_audit_plugin.py` 已删 |
| 删除 lifecycle plugin | `src/plugins/lifecycle/` 目录 + `tests/unit_v4/test_lifecycle_plugin.py` 已删 |
| 删除 activation plugin | `src/plugins/activation/` 目录，原本就无独立测试 |
| 删除 E2E 测试 | `tests/e2e/` 目录整体删除（17 个测试） |
| routing.py 简化 | 移除 resolve_agent, identify_nick, operators, default_agents；87 行 → 63 行 |
| router.py 简化 | 移除 _handle_explicit_command；159 行 → 161 行（因 resolve 合并到 mode plugin） |
| agent_mcp.py 简化 | 移除 fcntl lock；548 行 → 528 行 |
| feishu_chat_id → external_chat_id | ChannelRoute 字段重命名，解耦飞书依赖 |
| GroupManager + channel_chat_map | 新增 V4 routing.toml 映射支持（channel_id → feishu_chat_id） |
| mode plugin 扩展 | 新增 /resolve 命令 + channel_resolved 事件（原 lifecycle 功能合并） |

## 2. 环境状态

| 检查项 | 状态 |
|--------|------|
| `import channel_server` | OK |
| Python 版本 | 3.13.5 |
| uv 版本 | 0.7.15 |
| 依赖安装 | OK（mcp, irc, websockets, lark-oapi, zchat-protocol） |

## 3. 模块详情

### 3.1 Core Infrastructure

#### `src/channel_server/router.py` (161 行)

| 类/函数 | 行号 | 说明 |
|---------|------|------|
| `Router.__init__` | 25 | 接收 routing, registry, irc_conn, ws_server |
| `Router.forward_inbound_ws` | 39 | bridge→server：按 type 分派 message/event |
| `Router._handle_message` | 50 | 命令分派（"/" 前缀 → plugin）或路由到 IRC |
| `Router._route_to_irc` | 78 | 查 mode 决定 @ prefix，encode_msg 包装 |
| `Router._query_mode` | 114 | 通过 mode plugin 查当前 mode |
| `Router.forward_inbound_irc` | 124 | IRC→WS broadcast + plugin 订阅 + 命令分派 |
| `Router.emit_event` | 157 | core/plugin 发 event 统一出口 |

#### `src/channel_server/plugin.py` (106 行)

| 类 | 行号 | 说明 |
|----|------|------|
| `Plugin` (Protocol) | 13 | 接口定义：name, handles_commands, on_ws_message, on_ws_event, on_command, query |
| `BasePlugin` | 39 | 空默认实现基类 |
| `PluginRegistry` | 60 | 注册/查询/广播/容错。register() 时冲突检测 |

#### `src/channel_server/ws_server.py` (108 行)

| 类 | 行号 | 说明 |
|----|------|------|
| `BridgeConnection` | 23 | 已注册 bridge 连接数据类 |
| `WSServer` | 30 | WebSocket server：start/stop/broadcast/REGISTER 握手 |

#### `src/channel_server/routing.py` (63 行)

| 类/函数 | 行号 | 说明 |
|---------|------|------|
| `ChannelRoute` | 19 | channel 路由数据类（external_chat_id, agents） |
| `RoutingTable` | 27 | 路由表：channel_agents, external_chat_id 两个查询方法 |
| `load()` | 41 | 从 routing.toml 加载，文件不存在返回空表 |

#### `src/channel_server/irc_connection.py` (117 行)

| 类 | 行号 | 说明 |
|----|------|------|
| `IRCConnection` | 21 | IRC 客户端封装：connect/join/privmsg/send_sys/disconnect |

#### `agent_mcp.py` (528 行)

| 函数 | 行号 | 说明 |
|------|------|------|
| `chunk_message` | 47 | 按 UTF-8 字节数拆分消息（IRC RFC 2812） |
| `detect_mention` / `clean_mention` | 71/76 | @mention 检测和清理 |
| `inject_message` | 101 | IRC 消息 → MCP notification 注入 |
| `register_tools` | 151 | 注册 5 个 MCP tools |
| `_handle_reply` | 281 | reply tool：普通消息/编辑/side channel |
| `_handle_run_zchat_cli` | 334 | 执行 zchat CLI 命令 |
| `_start_irc` | 368 | 独立线程启动 IRC reactor |
| `main` | 446 | MCP stdio + IRC @mention 注入 |

**MCP Tools**: reply, join_channel, join_conversation, send_side_message, run_zchat_cli

### 3.2 Business Plugins（仅 2 个）

#### `src/plugins/mode/plugin.py` (67 行)

- **命令**: `/hijack`, `/release`, `/copilot`, `/resolve`
- **事件**: emit `mode_changed`, `channel_resolved`
- **query**: `get` → 返回 channel 当前 mode（copilot/takeover）
- **注**: /resolve 功能从已删除的 lifecycle plugin 合并而来

#### `src/plugins/sla/plugin.py` (96 行)

- **命令**: 无
- **订阅事件**: `mode_changed`（to=takeover → 启动 timer，其他 → 取消）
- **emit**: `sla_breach` event + `release` command（timer 到期）

### 3.3 Feishu Bridge (`src/feishu_bridge/`)

| 文件 | 行数 | 说明 |
|------|------|------|
| `bridge.py` | 566 | 主编排类：5 个飞书事件注册 + 入站/出站路由 |
| `outbound.py` | 220 | 出站路由：按 kind 路由到飞书群（msg→双写, side→仅squad） |
| `message_parsers.py` | 322 | 20+ 消息类型解析器（@register_parser 装饰器） |
| `test_client.py` | 262 | E2E 测试辅助：send/assert/poll 飞书 API |
| `group_manager.py` | 187 | 群角色管理 + V4 channel_chat_map 映射 |
| `sender.py` | 175 | 飞书 API 发送封装 |
| `ws_client.py` | 114 | CardAwareClient：补充 CARD 帧分发 |
| `bridge_api_client.py` | 104 | WebSocket 传输层 |
| `feishu_renderer.py` | 98 | 卡片 JSON 构建 |
| `config.py` | 87 | YAML 配置加载 + 环境变量替换 |

## 4. 测试结果

### 4.1 Unit 测试（114 passed / 0 failed）

| 测试文件 | 通过 | 覆盖 |
|----------|------|------|
| test_agent_mcp.py | 9 | 前缀使用 protocol、run_zchat_cli 行为、tool 注册 |
| test_mode_plugin.py | 10 | hijack/release/copilot/resolve 命令 + mode_changed/channel_resolved 事件 |
| test_plugin_registry.py | 12 | 注册/冲突/广播/容错 |
| test_router.py | 14 | WS↔IRC 路由全链路 + IRC 侧命令分派 |
| test_routing.py | 7 | TOML 解析/查询（external_chat_id） |
| test_sla_plugin.py | 6 | timer 生命周期 |
| test_card_action.py | 9 | CardAwareClient + CSAT/hijack/resolve |
| test_client_extended.py | 12 | FeishuTestClient 扩展方法 |
| test_group_manager.py | 13 | 角色/注册/持久化/成员 + channel_chat_map |
| test_outbound_router.py | 9 | V4 出站路由 |
| test_parsers.py | 8 | 消息类型解析 |
| test_sender.py | 3 | API 调用路径 |
| test_visibility_router.py | 2 | msg/side 路由 |

### 4.2 E2E 测试（已删除）

`tests/e2e/` 目录已整体删除。之前包含 17 个测试，现已由 pre_release 测试覆盖。

### 4.3 Pre-release 测试（14 tests, 未运行）

需要全栈 + 飞书凭证。覆盖：6步状态机、占位编辑、timer、CSAT、gate 隔离、admin 命令、SLA、授权模型。

## 5. 覆盖缺口

以下模块缺少独立单元测试：

| 模块 | 行数 | 现状 |
|------|------|------|
| `ws_server.py` | 108 | 通过 MockWSServer 间接覆盖 |
| `irc_connection.py` | 117 | 无测试（需要真实 IRC） |
| `__main__.py` | 113 | 进程入口，无测试 |
| `bridge.py` | 566 | 部分通过 test_card_action 覆盖 |
| `bridge_api_client.py` | 104 | 无测试 |
| `ws_client.py` | 114 | 部分通过 test_card_action 覆盖 |
| `config.py` | 87 | 无测试 |
| `feishu_renderer.py` | 98 | 通过 test_outbound_router 间接覆盖 |

## 6. 文件统计

- **总源文件**: 44
- **总行数**: 6,621
- **核心基础设施**: 672 行（router 161 + plugin 106 + ws_server 108 + routing 63 + irc_connection 117 + __main__ 113 + __init__ 4）
- **Agent MCP**: 528 行
- **插件**: 167 行（mode 67 + sla 96 + __init__ 合计 4）
- **Feishu Bridge**: 2,135 行
- **Unit 测试**: 1,180 行（unit_v4）+ 1,109 行（feishu tests）
- **Pre-release**: 807 行
