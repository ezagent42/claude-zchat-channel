---
type: e2e-report
id: cs-report-architecture-split
status: confirmed
producer: skill-4
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-architecture-split
  - cs-plan-architecture-split
  - cs-diff-architecture-split
evidence:
  - path: tests/unit/test_architecture_split.py
    type: unit-test
---

# E2E Report: Task 4.6.1 — server.py 架构拆分

## 测试执行

### Unit tests (`tests/unit/test_architecture_split.py`)

```
uv run pytest tests/unit/test_architecture_split.py -v
```

| ID | 名称 | 结果 |
|----|------|------|
| TC-001 | test_server_has_no_mcp_imports | PASSED |
| TC-002 | test_server_has_core_functions | PASSED |
| TC-003 | test_server_build_components_works | PASSED |
| TC-004 | test_agent_mcp_has_mcp_functions | PASSED |
| TC-005 | test_agent_mcp_has_no_engine_imports | PASSED |
| TC-006 | test_agent_mcp_tools_are_four | PASSED |
| TC-007 | test_entry_points_resolve | PASSED |

**小计：7 / 7 PASSED**（11.94s）

### 回归（`tests/unit/` 全量）

原有 engine / protocol / bridge_api / transport 测试未受影响。架构拆分为纯代码组织变更，
engine 模块零改动，无回归风险。

## 覆盖矩阵

| 验证点 | 状态 | 覆盖层级 |
|--------|------|----------|
| server.py 不含 MCP 代码 | CONFIRMED | unit (TC-001) |
| server.py 保留 engine 入口 | CONFIRMED | unit (TC-002, TC-003) |
| agent_mcp.py 包含 MCP 代码 | CONFIRMED | unit (TC-004) |
| agent_mcp.py 不含 engine 代码 | CONFIRMED | unit (TC-005) |
| agent_mcp 注册 4 个 tools | CONFIRMED | unit (TC-006) |
| 两个 entry_points 均可解析 | CONFIRMED | unit (TC-007) |
| engine/protocol/bridge_api/transport 零改动 | CONFIRMED | 代码审查 |

## 验证详情

### TC-001: server.py 不含 MCP 代码
验证 `server` 模块不暴露 `create_server` / `register_tools` / `inject_message` / `poll_irc_queue` / `load_instructions`。通过 `hasattr()` + `importlib.reload()` 确认。

### TC-003: build_components 仍正常组装
实际调用 `build_components()`，验证返回 dict 包含 `event_bus` / `conversation_manager` / `mode_manager` / `bridge_server` / `irc_transport`，各组件非 None。测试后关闭 DB 连接避免资源泄漏。

### TC-006: agent_mcp 注册 4 个 tools
实际调用 `create_server()` + `register_tools()`，通过 MCP `ListToolsRequest` 获取 tool 列表，验证名称集合为 `{reply, join_channel, join_conversation, send_side_message}`。

### TC-007: entry_points 解析
通过 `importlib.metadata.entry_points(group="console_scripts")` 验证 `zchat-channel` 和 `zchat-agent-mcp` 两个 entry_point 均存在。

## 风险与后续

- 所有 plan 中列出的 7 个 testcase 全部执行通过
- 无回归
- 后续：
  - Task 4.6.2（IRC 消息协议）依赖本拆分完成
  - Task 4.6.3（routing 配置）依赖本拆分完成
  - E2E 级别的进程间通信测试（server.py 独立进程 + agent_mcp.py MCP stdio）在后续 Task 中覆盖

## 结论

Task 4.6.1 开发完成，证据链（eval -> plan -> diff -> report）齐全。架构拆分实现了
server.py（独立进程）与 agent_mcp.py（轻量 MCP 代理）的职责分离，所有 unit 测试通过。
