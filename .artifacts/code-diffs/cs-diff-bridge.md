---
id: cs-diff-bridge
type: code-diff
status: implemented
phase: "Phase 3: Bridge API"
created_at: "2026-04-14"
related_ids:
  - cs-eval-bridge
  - cs-plan-bridge
---

# cs-diff-bridge — Bridge API 模块代码变更

## 新增文件

| 文件 | 行数 | 说明 |
|------|-----:|------|
| `bridge_api/__init__.py` | 1 | 包声明 |
| `bridge_api/ws_server.py` | 211 | `BridgeAPIServer` + `BridgeConnection` + visibility 路由表 + WebSocket 主循环 |

## 新增测试

| 文件 | 测试数 |
|------|-------:|
| `tests/unit/test_bridge_api.py` | 8 |

## 关键实现点

1. **`BridgeConnection` dataclass**：记录已注册 Bridge 的 bridge_type / instance_id / capabilities / websocket 句柄。
2. **`compute_visibility_targets(visibility)`**：静态方法，使用 `_VISIBILITY_ROUTING` 常量表返回 `set[str]`；未知 visibility 抛 `ValueError`。
3. **解析层与处理层分离**：
   - `_parse_register` / `_parse_operator_command` / `_parse_admin_command` 为纯函数，不触碰外部状态，便于单元测试；
   - `_handle_register` / `_handle_customer_connect` 负责调用上层 ConversationManager 或注册连接。
4. **WebSocket 主循环**：`_handle_connection` 按 `msg.type` 派发到注册 / customer / operator / admin 处理器；`on_customer_message` 等钩子由 server.py 组装时注入。
5. **`send_reply(conversation_id, text, visibility)`**：根据 `compute_visibility_targets` 在连接表里筛出匹配 `capabilities` 的 BridgeConnection 广播 JSON 回复，按 instance_id 去重。

## 依赖

- `websockets` — 已在 `pyproject.toml` 中声明，Phase 1 已验证可用
- `protocol.commands.parse_command` — Phase 1 交付

## 遗留 TODO

- Phase 4 (server.py) 接入时注入 `on_customer_message` / `on_operator_command` 等回调
- 真正的 WebSocket 握手 E2E 测试在 Phase 4 / 4.5 的集成阶段补全
