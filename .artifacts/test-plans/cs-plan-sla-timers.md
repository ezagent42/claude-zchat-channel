---
type: test-plan
id: cs-plan-sla-timers
status: confirmed
producer: skill-2
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-sla-timers
  - cs-diff-sla-timers
  - cs-report-sla-timers
---

# Test Plan: Task 4.6.7 SLA Timer 自动触发

## 来源

- eval-doc: `cs-eval-sla-timers`
- 代码改动范围：
  - 新增 `plugins/manager.py`（PluginManager + 扫描器）
  - 新增 `plugins/sla_app.py`（App plugin：on_conversation_created / on_agent_public_message / on_placeholder_sent / on_edit_sent）
  - `server.py` 在 `build_components` 中注入 plugin_manager；在 `_on_customer_connect` 末尾 + `send_reply` 后 fire hooks
  - 把 `SLA_ONBOARD_DURATION` 等默认时长写为模块级常量，支持测试 patch

## 用例列表

### Unit tests (`tests/unit/test_sla_timers.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-001 | test_sla_onboard_set_on_conversation_created | P0 | eval TC-1 | plugin fire 后 TimerManager 含 (conv, sla_onboard) |
| TC-002 | test_sla_onboard_breach_publishes_event | P0 | eval TC-2 | duration=0.1s，等待后 EventBus 有 TIMER_EXPIRED + sla.breach bridge event |
| TC-003 | test_sla_onboard_cancelled_by_agent_public_reply | P0 | eval TC-3 | public reply hook 后 timer 被 cancel |
| TC-004 | test_sla_onboard_not_cancelled_by_side_visibility | P1 | eval TC-4 | side 可见性 reply 不取消 onboard timer |
| TC-005 | test_plugin_manager_empty_plugins_dir | P1 | eval TC-5 | plugins/ 只含 README 时 hooks={} |
| TC-006 | test_plugin_manager_loads_sla_app | P1 | eval TC-6 | 加载 sla_app.py 后 4 个 hook 被注册 |
| TC-007 | test_sla_onboard_independent_per_conversation | P2 | eval TC-7 | 2 个 conv 并行各自独立 timer |

### E2E tests (`tests/e2e/test_sla_timers.py`)

| ID | 名称 | 优先级 | 验证 |
|----|------|--------|------|
| TC-E01 | test_sla_onboard_breach_e2e | P0 | customer_connect（缩短 duration 到 1s）→ 等待 → 收到 sla.breach event |

## 统计

- 总数：7 unit + 1 E2E = 8
- P0: 3 unit + 1 E2E = 4
- P1: 3 = 3
- P2: 1 = 1

## 实现要点

1. **`plugins/manager.py`**:
   - `PluginManager` 类：`__init__(plugins_dir: Path)` 扫描 `*.py`
   - 对每个模块，`inspect.getmembers(mod, callable)` 过滤 name.startswith("on_")
   - 注册到 `self._hooks: dict[str, list[Callable]]`
   - `async def fire(name, **kwargs)`：支持 sync/async 混合；忽略单个 hook 异常不影响其他
   - `hook_names()` 返回已注册的 hook 名集合

2. **`plugins/sla_app.py`**:
   ```python
   SLA_ONBOARD_DURATION_S = 3.0  # 可被测试 patch

   async def on_conversation_created(conv_id: str, components: dict) -> None:
       tm = components["timer_manager"]
       tm.set_timer(conv_id, "sla_onboard",
           timedelta(seconds=SLA_ONBOARD_DURATION_S),
           TimerAction(type="alert", params={"duration_s": SLA_ONBOARD_DURATION_S}))

   async def on_agent_public_message(conv_id: str, components: dict) -> None:
       components["timer_manager"].cancel_timer(conv_id, "sla_onboard")
   ```

3. **`server.py` 改动**:
   - `build_components()`: `plugin_manager = PluginManager(Path(__file__).parent / "plugins")`
   - `_on_customer_connect`: 末尾 `await plugin_manager.fire("on_conversation_created", conv_id=conv_id, components=components)`
   - 新增 helper：`_after_reply(conv_id, visibility)` 在 operator/agent 回复后 fire `on_agent_public_message`（当 visibility=="public"）

4. **Agent 的 public 消息判定**：
   - 当前架构下，agent 的 reply 通过 MCP → IRC PRIVMSG → channel-server 未直接拦截
   - MVP：暴露显式 hook `on_agent_public_message`，由测试直接调用
   - 完整实现（Phase Final）：IRC bot 观察到 non-self 的 PRIVMSG 时 fire

## 风险

- agent public message hook 的实际触发点需要后续接入 IRC 监听（MVP 仅提供 hook API）
- 3s 默认 SLA 太短，E2E 测试需要缩短到 1s

## Merge 条件

- 所有 8 个用例 PASS
- 现有 168 tests 全部 PASS（0 回归）
