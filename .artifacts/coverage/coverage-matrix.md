# Coverage Matrix · zchat-channel-server

> Bootstrap 2026-04-21 (V6 finalize 后)。

## 1. 代码测试覆盖

| Module | Source | Tests | 状态 |
|---|---|---|---|
| channel_server | `src/channel_server/` (router, routing, irc_connection, plugin, ws_server, __main__) | `tests/unit/test_router*.py`, `test_routing*.py`, `test_ws_server.py`, `test_plugin.py` | ✅ 核心路由 + NAMES 熔断 |
| agent_mcp | `agent_mcp.py` 单文件 | `tests/unit/test_agent_mcp*.py` | ✅ reply/run_zchat_cli/list_peers |
| feishu_bridge | `src/feishu_bridge/` (bridge, outbound, sender, renderer, mapper, test_client, routing_reader) | `tests/unit/test_outbound_router.py`, `test_group_manager.py`, `test_feishu_renderer*.py`, `test_card_action.py` | ✅ supervise + CSAT + chat_info |
| plugins | `src/plugins/` (mode/sla/resolve/audit/activation/csat) | 每 plugin 有 `tests/unit/test_<name>_plugin.py` | ✅ 6 plugin |
| tests | 自指 | — | — |
| meta | pyproject, instructions.md 等 | — | 配置 |

**Unit baseline**: 183 passed / 0 failed / 0 skipped。

## 2. E2E 覆盖

`tests/e2e/` 12 个用例，pytest -m e2e 触发。
**当前 4 个失败**（V6 重构后 stale，Stage 4 修）：
- `test_csat_lifecycle.py::test_csat_full_lifecycle` - CSAT 从 message 通道改 event，fixture 未更新
- `test_csat_lifecycle.py::test_csat_multiple_channels` - 同上
- `test_help_request_lifecycle.py::test_operator_responds_in_time` - help_requested emit 时机 + source marker 改变
- `test_help_request_lifecycle.py::test_operator_no_response_emits_timeout` - reason 从 `operator_no_response` → `no_human_response`，fixture 未同步

## 3. 架构红线

core (`src/channel_server/`) **0 代码命中**，1 处注释提到业务场景（`router.py:70-71`，Stage 4 整改）。业务语义集中在：
- `src/feishu_bridge/` — 业务 bridge（允许）
- `src/plugins/` — 业务 plugin（audit/csat 内部有业务名，但跨层不泄露）

## 4. 依赖隔离

`tests/unit/test_plugin.py::test_no_import_of_channel_server` 强制 plugins/ 不依赖 channel_server。已过。

## 5. 已知缺口

- E2E 4 failing (Stage 4 修)
- router.py:70-71 注释业务名（Stage 4 整改）
