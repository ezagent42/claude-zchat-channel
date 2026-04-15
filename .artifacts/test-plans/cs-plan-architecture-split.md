---
type: test-plan
id: cs-plan-architecture-split
status: executed
producer: skill-2
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-architecture-split
  - cs-diff-architecture-split
---

# Test Plan: Task 4.6.1 — server.py 架构拆分

## 来源

- eval-doc: `cs-eval-architecture-split`
- plan: `docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.1
- code 改动范围：`server.py`（去 MCP）+ `agent_mcp.py`（新建）+ `pyproject.toml`（新 entry_point）

## 用例列表

### Unit tests (`tests/unit/test_architecture_split.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-001 | test_server_has_no_mcp_imports | P0 | eval #1 | server.py 不含 create_server / register_tools / inject_message / poll_irc_queue / load_instructions |
| TC-002 | test_server_has_core_functions | P0 | eval #2 | server.py 保留 build_components / wire_bridge_callbacks / entry_point / main |
| TC-003 | test_server_build_components_works | P0 | eval #7 | build_components() 能组装所有 engine 组件且无报错 |
| TC-004 | test_agent_mcp_has_mcp_functions | P0 | eval #3 | agent_mcp.py 包含 create_server / register_tools / inject_message / poll_irc_queue / load_instructions / entry_point |
| TC-005 | test_agent_mcp_has_no_engine_imports | P0 | eval #4 | agent_mcp.py 不含 build_components / wire_bridge_callbacks |
| TC-006 | test_agent_mcp_tools_are_four | P0 | eval #5 | agent_mcp 注册 4 个 tools: reply, join_channel, join_conversation, send_side_message |
| TC-007 | test_entry_points_resolve | P0 | eval #6 | zchat-channel 和 zchat-agent-mcp 两个 console_scripts entry_point 均可解析 |

## 统计

- 总数：7 unit
- P0: 7（架构拆分是基础设施变更，所有用例均为 P0）
- P1: 0
- P2: 0

## 验证策略

1. **模块属性检查**：通过 `hasattr()` 验证模块职责边界 — server.py 不暴露 MCP 函数，agent_mcp.py 不暴露 engine 函数。
2. **功能可用性**：`build_components()` 实际调用验证 engine 组装链完整；`create_server()` + `register_tools()` 实际调用验证 MCP tool 注册链完整。
3. **Entry point 解析**：通过 `importlib.metadata.entry_points()` 验证 `pyproject.toml` 声明正确。

## 风险

- `test_server_build_components_works` 需要 SQLite 临时路径（通过 `tmp_path` + monkeypatch 环境变量解决）。
- `test_agent_mcp_tools_are_four` 需要 `mcp` 库可用（开发依赖已安装）。

## 实现要点

1. 所有测试在 `tests/unit/test_architecture_split.py` 中，不需要 IRC/Bridge 等外部依赖。
2. `autouse` fixture 设置 `CS_DB_PATH` / `CS_EVENT_DB_PATH` / `CS_MESSAGE_DB_PATH` / `BRIDGE_PORT=0` / `AGENT_NAME=unit-agent` 避免污染。
3. `importlib.reload(server)` 确保每个测试拿到干净的模块状态。
