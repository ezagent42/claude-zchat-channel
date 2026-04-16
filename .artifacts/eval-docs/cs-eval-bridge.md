---
id: cs-eval-bridge
type: eval-doc
status: confirmed
phase: "Phase 3: Bridge API"
created_at: "2026-04-14"
related_ids:
  - cs-plan-bridge
---

# cs-eval-bridge — Bridge WebSocket API（多角色接入）

## 特性描述

实现 channel-server 的 `bridge_api/` 模块。Bridge API 是 channel-server 对外的
WebSocket 接口，所有人类用户（客户、客服 operator、管理员 admin）都通过 Bridge
接入；IRC 仅作内部 transport 连接 agent。

## 期望行为

| 能力 | 行为 |
|------|------|
| Bridge 注册 | `register` 消息创建 `BridgeConnection`，记录 bridge_type / instance_id / capabilities |
| Customer 接入 | `customer_connect` 消息调用 `ConversationManager.create()`，不直接下发任何回复 |
| Operator 命令 | `operator_command`（含 `/hijack` 等）通过 `protocol.commands.parse_command()` 解析为 `Command` |
| Admin 命令 | `admin_command`（含 `/status`、`/dispatch`、`/assign`）同样走 `parse_command()` |
| Visibility 路由 | `public → {customer, operator, admin}`；`side / system → {operator, admin}` |

## 设计约束

- 解析函数（`_parse_register` / `_parse_operator_command` / `_parse_admin_command`）必须是纯函数，便于单元测试
- `compute_visibility_targets` 必须是 classmethod/staticmethod，测试不依赖实例
- `side` 消息**必须**经过 Bridge API 才能送达 operator（spec §5 修正过的路由规则）
- 模块只做 transport + 分发，不包含业务状态机（业务逻辑由 ConversationManager / Gate 处理）

## Spec 参考

- `spec/channel-server/02-channel-server.md` §5 Bridge API（完整消息格式）
- `spec/channel-server/03-bridge-layer.md`
