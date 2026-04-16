---
id: cs-report-bridge
type: e2e-report
status: pass
phase: "Phase 3: Bridge API"
created_at: "2026-04-14"
related_ids:
  - cs-diff-bridge
---

# cs-report-bridge — Bridge API 模块测试报告

## 执行环境

- Python: 3.13.5
- pytest: 9.0.2
- Platform: linux (WSL2)
- Submodule branch: `feat/bridge-api`（基于 `feat/protocol`）

## 测试结果

```
============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.0.2, pluggy-1.6.0
configfile: pytest.ini
plugins: asyncio-1.3.0, anyio-4.12.1
collected 8 items

tests/unit/test_bridge_api.py::test_parse_register PASSED                [ 12%]
tests/unit/test_bridge_api.py::test_customer_connect PASSED              [ 25%]
tests/unit/test_bridge_api.py::test_operator_command_hijack PASSED       [ 37%]
tests/unit/test_bridge_api.py::test_admin_command_status PASSED          [ 50%]
tests/unit/test_bridge_api.py::test_visibility_routing_public PASSED     [ 62%]
tests/unit/test_bridge_api.py::test_visibility_routing_side PASSED       [ 75%]
tests/unit/test_bridge_api.py::test_visibility_routing_system PASSED     [ 87%]
tests/unit/test_bridge_api.py::test_register_creates_connection PASSED   [100%]

========================= 8 passed, 1 warning in 0.14s =========================
```

| 指标 | 值 |
|------|---:|
| **PASS** | 8 |
| **FAIL** | 0 |
| **SKIP** | 0 |
| **ERROR** | 0 |

## 按文件明细

| 测试文件 | PASS | FAIL | SKIP |
|----------|-----:|-----:|-----:|
| `tests/unit/test_bridge_api.py` | 8 | 0 | 0 |

## TC 映射（与 cs-plan-bridge）

| TC-ID | 测试 | 状态 |
|-------|------|:----:|
| TC-01 | `test_parse_register` | PASS |
| TC-02 | `test_customer_connect` | PASS |
| TC-03 | `test_operator_command_hijack` | PASS |
| TC-04 | `test_admin_command_status` | PASS |
| TC-05 | `test_visibility_routing_public` | PASS |
| TC-06 | `test_visibility_routing_side` | PASS |
| TC-07 | `test_visibility_routing_system` | PASS |
| TC-08 | `test_register_creates_connection` | PASS |

## 完整性对照（plan §完成标准）

- [x] `bridge_api/ws_server.py` 实现完整
- [x] 8 个测试 PASS
- [x] 支持 customer / operator / admin 三种角色消息
- [x] visibility 路由正确（public→全部，side→operator+admin，system→operator+admin）
- [ ] commit（由人类操作 merge；agent 仅确保链条完整）

## 回归确认

全模块单元测试（`uv run pytest tests/unit/ -v`）54 passed，无回归：
Phase 1 protocol 40 + Phase 3 bridge 8 + legacy 6 = 54 PASS / 0 FAIL / 0 SKIP。

## 警告

- `websockets.legacy` 的 DeprecationWarning 来自第三方包自身导入，属上游 14.0 迁移提示，不影响功能；后续 Phase 4 接入时统一迁移到新版 API。
