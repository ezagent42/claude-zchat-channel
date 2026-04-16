---
type: test-plan
id: cs-plan-engine
status: executed
producer: skill-2
consumers: [skill-3, skill-4]
created_at: "2026-04-14T00:00:00Z"
related:
  - eval-doc: cs-eval-engine
  - code-diff: cs-diff-engine
---

# Test Plan: channel-server engine 模块

## 测试范围

`zchat-channel-server/engine/` 下 8 个运行时模块：
- event_bus, conversation_manager, mode_manager, timer_manager
- message_store, plugin_manager, participant_registry, squad_registry

## 测试层级

全部为 **unit** 层（pytest + pytest-asyncio auto），使用 `tmp_path` 隔离 SQLite 文件。

## 测试文件与 TC 矩阵

| 文件 | 测试数 | 覆盖 TC |
|------|-------|---------|
| `tests/unit/test_event_bus.py` | 6 | TC-01..05 + 持久化跨实例 |
| `tests/unit/test_conversation_manager.py` | 13 | TC-06..13 + remove/非-operator/懒加载 closed |
| `tests/unit/test_mode_manager.py` | 4 | TC-14..17 |
| `tests/unit/test_timer_manager.py` | 5 | TC-18..21 + 过期后清理 |
| `tests/unit/test_message_store.py` | 7 | TC-22..24 + 顺序/持久化/未知 edit |
| `tests/unit/test_plugin_manager.py` | 5 | TC-25..28 + 空目录 |
| `tests/unit/test_participant_registry.py` | 7 | TC-29..31 + 幂等/unregister |
| `tests/unit/test_squad_registry.py` | 7 | TC-32..34 + unassign/未知查询 |

## 执行命令

```bash
cd zchat-channel-server
uv run pytest tests/unit/ -v
```

## 通过标准

- 全部 8 个新增测试文件 PASS
- 已有 protocol/ 40 个测试不 regression
- **合计 94 个 unit test PASS（40 protocol 回归 + 54 engine 新增），0 FAIL，0 SKIP**
