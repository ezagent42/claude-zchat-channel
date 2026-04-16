---
id: cs-plan-bridge
type: test-plan
status: executed
phase: "Phase 3: Bridge API"
created_at: "2026-04-14"
related_ids:
  - cs-eval-bridge
  - cs-diff-bridge
---

# cs-plan-bridge — Bridge API 模块测试计划

## 测试文件清单

| 文件 | 测试数 | 覆盖模块 |
|------|--------|----------|
| `tests/unit/test_bridge_api.py` | 8 | `bridge_api/ws_server.py`（BridgeAPIServer, BridgeConnection） |

**总计: 8 个测试用例**

## 测试用例

| TC-ID | 用例 | 覆盖点 |
|-------|------|--------|
| TC-01 | `test_parse_register` | `_parse_register` 返回的 BridgeConnection 含 bridge_type 与 capabilities |
| TC-02 | `test_customer_connect` | `_handle_customer_connect` 调用 `ConversationManager.create()` 一次 |
| TC-03 | `test_operator_command_hijack` | `/hijack` 经 `_parse_operator_command` 解析为 `Command(name="hijack")` |
| TC-04 | `test_admin_command_status` | `/status` 经 `_parse_admin_command` 解析为 `Command(name="status")` |
| TC-05 | `test_visibility_routing_public` | `compute_visibility_targets("public") == {customer, operator, admin}` |
| TC-06 | `test_visibility_routing_side` | `compute_visibility_targets("side") == {operator, admin}` |
| TC-07 | `test_visibility_routing_system` | `compute_visibility_targets("system") == {operator, admin}` |
| TC-08 | `test_register_creates_connection` | register 时 instance_id / capabilities 被正确保留 |

## 覆盖缺口

- WebSocket 端到端握手由 Phase 4 (server.py) + Phase 4.5 (feishu bridge) 的集成测试覆盖
- `send_reply` 的实际转发行为（websocket.send）需要 asyncio E2E 测试，放在 Phase 4 阶段

## 执行方式

```bash
cd zchat-channel-server
uv run pytest tests/unit/test_bridge_api.py -v
```

Expected: 8 passed, 0 failed, 0 skipped.
