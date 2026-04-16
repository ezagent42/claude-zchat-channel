---
id: cs-diff-server-v2
type: code-diff
status: implemented
phase: "Phase 4 补全: mode switching + gate enforcement"
created_at: "2026-04-15"
related_ids:
  - cs-eval-server-v2
  - cs-plan-server-v2
  - cs-test-diff-server-v2
  - cs-report-server-v2
---

# cs-diff-server-v2 — Phase 4 补全实现差异

> 分支: `feat/server-v1` @ `zchat-channel-server` submodule
> 基线: cs-diff-server（Phase 4 首次提交 e89ced6）

## 文件改动

### 修改

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `bridge_api/ws_server.py` | 新增方法 + 新增槽 | `on_operator_join` 回调槽；`send_event()` 广播方法；`_handle_connection` 新增 `operator_join` 分支 |
| `server.py` | 新增函数 + 修改 main | `wire_bridge_callbacks()` 注入 on_operator_join + on_operator_command；main() 调用注入 |
| `tests/unit/test_bridge_api.py` | 追加 3 条 unit | TC-U12/13/14 |
| `tests/unit/test_server_integration.py` | 追加 1 条 unit | TC-U15 |
| `tests/e2e/test_gate_enforcement.py` | 修改（1 条测试修复） | TC-E07：drain customer_ws 积压 mode.changed 广播事件后再断言 |

### 新建

| 文件 | 说明 |
|------|------|
| `tests/e2e/test_mode_switching.py` | TC-E05 + TC-E06（operator_join / hijack 触发模式切换） |
| `tests/e2e/test_gate_enforcement.py` | TC-E07 + TC-E08（side visibility 路由 + mode.changed 广播） |

## 关键设计决策

1. **`send_event()` 无 visibility 过滤**：mode.changed 是协议级状态广播，所有 Bridge 包括
   customer 都需要感知（否则 customer UI 无法更新状态）。与 `send_reply()` 的 visibility
   路由严格区分。
2. **`wire_bridge_callbacks()` 独立函数**：从 main() 中分离，便于 TC-U15 单元测试不启动
   IRC/WebSocket 即可验证注入是否完成。
3. **gate enforcement 验证路径**：不直接 mock IRC agent 发消息（需要 MCP + Claude），而是
   借助 `/hijack` callback 主动调用 `send_reply(visibility="side")` 作为系统通知，
   在真实 E2E 环境中验证 visibility 路由。

## 测试结果

- 新增 8 条（4 unit + 4 E2E）全 PASS
- 原有 117 条无回归
- 总计 **125 passed / 0 failed / 0 skipped**

## 调试修复记录

TC-E07 首次失败：`customer_ws.recv()` 未超时失败。
根因：`send_event()` 广播 mode.changed 到所有连接，customer_ws buffer 有积压事件。
修复：在断言 customer_ws 静默前，先 drain 掉 2 个积压的 mode.changed 广播事件。
