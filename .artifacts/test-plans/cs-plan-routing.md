---
type: test-plan
id: cs-plan-routing
status: executed
producer: skill-2
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-routing
  - cs-diff-routing
---

# Test Plan: Task 4.6.3 — Routing 配置

## 来源

- eval-doc: `cs-eval-routing`
- plan: `docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.3
- code 改动范围：`routing_config.py`（新建）+ `server.py`（集成 routing config）

## 用例列表

### Unit tests (`tests/unit/test_routing_config.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-001 | test_load_routing_config_full | P0 | eval #1 | 正确解析 [routing] 段的 default_agents / escalation_chain / available_agents |
| TC-002 | test_load_missing_config | P0 | eval #2 | 文件不存在 → 返回空默认 RoutingConfig |
| TC-003 | test_load_empty_config | P1 | eval #3 | 空 TOML → 空默认配置 |
| TC-004 | test_load_partial_config | P0 | eval #4 | 只有 default_agents → 其余字段为空列表 |
| TC-005 | test_load_malformed_config | P1 | eval #5 | 格式错误 TOML → 默认配置（不崩溃） |
| TC-006 | test_dispatch_whitelist_pass | P0 | eval #6 | agent 在白名单 → True |
| TC-007 | test_dispatch_whitelist_reject | P0 | eval #7 | agent 不在白名单 → False |
| TC-008 | test_dispatch_empty_whitelist | P0 | eval #8 | 白名单为空 → 不限制 |
| TC-009 | test_dispatch_default_config | P0 | eval #9 | 默认配置 → 不限制 |

### E2E tests (`tests/e2e/test_routing.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-R01 | test_auto_dispatch_on_create | P0 | eval #1 | customer_connect → default_agents 自动 dispatch → agent.dispatched event |
| TC-R02 | test_dispatch_whitelist_reject_e2e | P0 | eval #7 | /dispatch 不在白名单的 agent → 拒绝 reply |
| TC-R03 | test_dispatch_whitelist_pass_e2e | P0 | eval #6 | /dispatch 在白名单的 agent → agent.dispatched event |

## 统计

- 总数：9 unit + 3 E2E = 12
- P0: 10
- P1: 2

## 验证策略

1. **TOML 解析**：使用 `tempfile.NamedTemporaryFile` 创建临时 TOML 文件，调用 `load_routing_config()` 验证解析结果。
2. **白名单逻辑**：直接构造 `RoutingConfig` 实例，调用 `is_dispatch_allowed()` 验证返回值。
3. **E2E 集成**：启动带 `CS_ROUTING_CONFIG` 环境变量的 channel-server，通过 Bridge WebSocket 发送 customer_connect 和 admin_command，验证自动 dispatch 和白名单行为。

## 风险

- E2E 测试需要独立的 `channel_server_with_routing` fixture（带 routing.toml 的自定义环境变量）。
- `test_load_malformed_config` 依赖 tomllib/tomli 的错误处理行为。

## 实现要点

1. Unit 测试使用 `tempfile.NamedTemporaryFile(suffix=".toml")` 避免文件系统污染。
2. `RoutingConfig` 为 `frozen=True` dataclass，immutable 设计简化测试。
3. E2E fixture `routing_toml` 在 `tmp_path` 中创建配置文件，通过 `CS_ROUTING_CONFIG` 环境变量传递给 server 进程。
