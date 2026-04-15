---
type: code-diff
id: cs-diff-commands
status: confirmed
producer: skill-3
created_at: "2026-04-15"
related:
  - cs-plan-commands
  - cs-eval-commands
---

# Code Diff: 命令 Handler 补全 — /resolve /status /dispatch

## 修改文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `server.py` | M (+55 行) | wire_bridge_callbacks() 新增 3 个回调 |

## 新增文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `tests/unit/test_command_handlers.py` | ~200 | 10 个 unit tests |
| `tests/e2e/test_command_handlers.py` | ~115 | 3 个 E2E tests |

## 具体改动

### server.py:wire_bridge_callbacks()

1. `_on_operator_command`: 新增 `/resolve` 分支（activate if CREATED → resolve → event + CSAT reply）
2. `_on_admin_command`: 新回调（/status → list_active 格式化, /dispatch → add_participant + event）
3. `_on_customer_message`: 新回调（CSAT score 接收 → set_csat）
4. 回调注册：新增 `bridge_server.on_admin_command` + `bridge_server.on_customer_message`

## 影响范围

- 仅修改 server.py 胶水层，不修改 engine/ bridge_api/ protocol/
- 回归风险：低（新增 elif 分支，不影响现有 /hijack /release /copilot）
