---
type: eval-doc
id: cs-eval-routing
status: confirmed
mode: verify
feature: "Routing 配置 — routing.toml 加载 + 白名单验证 + auto-dispatch"
producer: skill-5
submitter: yaosh
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-plan-routing
  - cs-diff-routing
  - cs-report-routing
---

# Eval: Task 4.6.3 — Routing 配置

## 背景

channel-server 需要支持多 agent 编排：新 conversation 自动分配 agent、升级链路 fallback、
`/dispatch` 命令白名单限制。配置通过 `routing.toml` 文件加载，数据结构为 `RoutingConfig`
dataclass。

需求来源：`docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.3。

## 配置格式

```toml
[routing]
default_agents = ["fast-agent"]              # 新 conversation 自动 dispatch
escalation_chain = ["deep-agent", "operator"] # 升级时按顺序 fallback
available_agents = ["fast-agent", "deep-agent", "translation-agent"]  # /dispatch 白名单
```

## 代码位置

- `routing_config.py:24-37` — `RoutingConfig` dataclass（frozen=True）
  - `default_agents: list[str]`
  - `escalation_chain: list[str]`
  - `available_agents: list[str]`
  - `is_dispatch_allowed(agent_nick)` — 白名单校验
- `routing_config.py:39-58` — `load_routing_config(path)` — TOML 解析 + 容错降级

## 行为预期

| # | 预期 | 状态 |
|---|------|------|
| 1 | 正确解析 routing.toml 中 [routing] 段的三个列表字段 | CONFIRMED |
| 2 | 文件不存在 → 返回空默认 RoutingConfig（不报错） | CONFIRMED |
| 3 | 空 TOML 文件 → 返回空默认配置 | CONFIRMED |
| 4 | 只有部分字段 → 缺失字段用默认空列表 | CONFIRMED |
| 5 | 格式错误的 TOML → 返回默认配置（不崩溃） | CONFIRMED |
| 6 | `is_dispatch_allowed()` 白名单非空 + agent 在列表 → True | CONFIRMED |
| 7 | `is_dispatch_allowed()` 白名单非空 + agent 不在列表 → False | CONFIRMED |
| 8 | `is_dispatch_allowed()` 白名单为空 → 不限制，任何 agent 都 True | CONFIRMED |
| 9 | 默认 RoutingConfig（无白名单） → 不限制 | CONFIRMED |

## 风险

- **低风险**：routing_config.py 是纯配置加载模块，不涉及网络或并发。
- TOML 解析失败时 graceful 降级为默认值，不会阻塞 server 启动。
- `tomllib`（3.11+）或 `tomli`（兼容）的导入已处理。

## 验证范围

- 覆盖：9 个 unit 测试（`tests/unit/test_routing_config.py`），验证 TOML 解析 + 白名单逻辑。
- E2E：3 个 E2E 测试（`tests/e2e/test_routing.py`），验证 auto-dispatch + 白名单 reject/pass。
- 不覆盖：escalation_chain 实际触发（属于 timer/escalation 模块，Task 4.6.7 覆盖）。
