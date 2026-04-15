---
type: code-diff
id: cs-diff-routing
status: confirmed
producer: skill-3
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-routing
  - cs-plan-routing
  - cs-report-routing
---

# Code Diff: Task 4.6.3 — Routing 配置

## 来源

- plan: `cs-plan-routing`
- eval-doc: `cs-eval-routing`
- 背景：channel-server 需要从 routing.toml 加载多 agent 编排配置（auto-dispatch / escalation / 白名单），支持启动时配置和运行时校验。

## 变更文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `routing_config.py` | A | 新建 — RoutingConfig dataclass + load_routing_config() + is_dispatch_allowed() |
| `server.py` | M | 集成 routing config：build_components 加载 routing.toml，/dispatch 白名单校验，customer_connect auto-dispatch |
| `tests/unit/test_routing_config.py` | A | 9 个 unit 测试 |
| `tests/e2e/test_routing.py` | A | 3 个 E2E 测试（auto-dispatch + 白名单 reject/pass） |

## 改动类型

### routing_config.py（Added — 新建文件，~59 行）

**`RoutingConfig` dataclass（:24-37，frozen=True）**:
- `default_agents: list[str]` — 新 conversation 自动 dispatch 的 agent 列表
- `escalation_chain: list[str]` — 升级时按顺序 fallback 的 agent 列表
- `available_agents: list[str]` — /dispatch 命令白名单（空 = 不限制）
- `is_dispatch_allowed(agent_nick: str) -> bool` — 白名单校验：空列表 → True，非空 → nick in list

**`load_routing_config(path) -> RoutingConfig`（:39-58）**:
- 文件不存在 → `logger.info` + 返回空默认值
- TOML 解析失败 → `logger.warning` + 返回空默认值
- 正常解析 → 从 `data["routing"]` 段提取三个列表字段
- 兼容 Python 3.11+ `tomllib` 和 3.10 `tomli`

### server.py（Modified — 集成 routing）

**环境变量**:
- `CS_ROUTING_CONFIG` — routing.toml 文件路径（默认 `routing.toml`）

**build_components() 变更**:
- 调用 `load_routing_config(CS_ROUTING_CONFIG)` 并存入 components dict

**wire_bridge_callbacks() 变更**:
- `/dispatch` 命令 handler 增加白名单校验：
  ```python
  if not routing_config.is_dispatch_allowed(agent_nick):
      await bridge_server.send_reply(..., text=f"rejected: {agent_nick} not in available_agents")
      return
  ```
- `_on_customer_connect` 增加 auto-dispatch：
  ```python
  for agent in routing_config.default_agents:
      await bridge_server.send_event("agent.dispatched", {...}, ...)
  ```

## 影响模块

- `routing_config.py` — 新增模块
- `server.py` — 集成 routing config
- 测试套件 — 新增 9 unit + 3 E2E

**零改动模块**：engine/ protocol/ bridge_api/ transport/ agent_mcp.py message.py

## 风险评估

- **低风险**：routing_config.py 为纯配置模块，所有 I/O 限于文件读取，失败时 graceful 降级。
- /dispatch 白名单为 additive 约束（空白名单 = 不限制），不会破坏已有行为。
- auto-dispatch 仅在 default_agents 非空时触发，空配置时无额外行为。
