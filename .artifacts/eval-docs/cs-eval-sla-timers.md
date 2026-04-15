---
type: eval-doc
id: cs-eval-sla-timers
status: confirmed
mode: simulate
feature: SLA Timer 自动触发 — App plugin hooks (sla_onboard / sla_placeholder / sla_slow_query)
producer: skill-5
submitter: yaosh
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-plan-sla-timers
  - cs-diff-sla-timers
  - cs-report-sla-timers
---

# Eval: Task 4.6.7 SLA Timer 自动触发

## 背景

spec `06-gap-fixes.md` 修复 1 定义了 3 个 App 层 SLA timer:

| Timer | 默认时长 | 设置时机 | 取消时机 |
|-------|---------|---------|---------|
| `sla_onboard` | 3s | conversation.created | 首条 agent public 消息 |
| `sla_placeholder` | 1s | 复杂查询检测后 | 占位消息发出 |
| `sla_slow_query` | 15s | 占位消息发出后 | edit_message 调用 |

TimerManager 底层 WORKING（`set_timer` / `cancel_timer` / `TIMER_EXPIRED`）。`_on_sla_breach` 处理器已接在 `EventBus.TIMER_EXPIRED`。**缺口**：没有 App 层 plugin 在正确时机调用 TimerManager.set_timer/cancel_timer。

需求来源：`docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.7。

## 行为预期

1. **sla_onboard**
   - conversation.created 事件发出 → 自动 set timer(3s)
   - 3s 内无 agent public 消息 → 发 sla.breach event + admin alert
   - 3s 内有 agent public 消息 → cancel timer（静默）

2. **sla_placeholder / sla_slow_query**
   - v1.0 MVP 范围：sla_onboard（核心 KPI）
   - placeholder/slow_query 由 App 特定语义触发（复杂查询检测），作为可扩展 hook 保留但不强制启用
   - 通过 plugin hook `on_placeholder_sent` / `on_edit_sent` 暴露 API

## PluginManager 设计

**最小可行 PluginManager**:
- 加载 `plugins/*.py` 中所有以 `on_` 开头的可调用
- 提供 `fire(hook_name, **kwargs)` 同步/异步分发
- 挂在 `components["plugin_manager"]`

**入口 hooks**:
- `on_conversation_created(conv_id, components)` — 在 `_on_customer_connect` 末尾触发
- `on_agent_public_message(conv_id, components)` — 在 send_reply(visibility=public) 后触发（agent 回复确认）
- `on_placeholder_sent(conv_id, components)` — 由 App 显式调用
- `on_edit_sent(conv_id, components)` — 在 send_edit 后触发

**App plugin**（`plugins/sla_app.py`）实现所有 `on_*` 函数。

## Testcase（simulate 模拟）

| # | 场景 | 前置条件 | 操作 | 预期 | 模拟效果 | 差异 | 优先级 |
|---|------|---------|------|------|---------|------|--------|
| TC-1 | sla_onboard 自动设置 | 空 TimerManager | customer_connect | TimerManager 内有 (conv_id, "sla_onboard") 任务 | OK，在 _on_customer_connect 末尾调用 plugin_manager.fire | 无 | P0 |
| TC-2 | sla_onboard 超时告警 | sla_onboard 已设置 | 等待 3s 无 agent 消息 | sla.breach event + admin alert（`_on_sla_breach` 已处理） | OK | 无 | P0 |
| TC-3 | sla_onboard 正常取消 | sla_onboard 已设置 | agent 发 visibility=public reply | timer 被 cancel，无 breach | OK，在 send_reply 后 fire on_agent_public_message | 无 | P0 |
| TC-4 | side visibility 不取消 onboard | sla_onboard 已设置 | operator 发 visibility=side reply | timer 仍在，无 cancel | OK，hook 只在 public 触发 | 无 | P1 |
| TC-5 | PluginManager 加载空目录 | plugins/ 仅有 README | build_components | plugin_manager.hooks 为空，fire 是 no-op | OK | 无 | P1 |
| TC-6 | PluginManager 发现 on_* 函数 | plugins/ 含 sla_app.py | build_components | plugin_manager 注册 4 个 hook 函数 | OK | 无 | P1 |
| TC-7 | 多个 conv 并行 sla_onboard | 2 个 customer_connect 并行 | - | 2 个独立 timer，互不干扰 | OK，TimerManager 按 (conv, name) 隔离 | 无 | P2 |
| TC-8 | E2E: customer_connect 无 agent 回应 → 超时告警 | ergo + channel-server 运行 | customer_connect + 等待 ≥3s | 收到 sla.breach event（breach_type=sla_onboard） | OK | 需 3s+ 等待，测试用缩短 timer 到 1s | P0 |

## 风险

- **TC-3 切入点**：`send_reply` 在 `_on_operator_command`/`_on_admin_command`/`_on_sla_breach` 等多处调用，但 agent 的 "public" reply 具体来自 MCP `reply()` → bridge_server.send_reply。解法：在 `send_reply` 后统一 fire hook（传 visibility），由 plugin 判断是否 cancel。
- **Plugin 加载时机**：在 `build_components` 构建 plugin_manager 并扫描 plugins/，避免运行时 IO。
- **测试缩短 timer**：unit 测试用 `duration=0.1s` 直接 patch `SLA_ONBOARD_DURATION` 常量，避免 3s 等待。

## 验证范围

- 覆盖：unit 7 个（TC-1~7），E2E 1 个（TC-8）
- 不覆盖：sla_placeholder/sla_slow_query 的实际业务触发（App 层语义，留扩展点）
