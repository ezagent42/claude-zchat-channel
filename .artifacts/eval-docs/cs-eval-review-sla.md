---
type: eval-doc
id: cs-eval-review-sla
status: confirmed
mode: verify
feature: "/review 命令统计 + SLA breach 自动告警"
producer: skill-5
submitter: yaosh
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-plan-review-sla
  - cs-diff-review-sla
  - cs-report-review-sla
---

# Eval: Task 4.6.4 — /review 命令 + SLA breach 告警

## 背景

运营管理员需要两个能力：
1. **`/review` 命令** — 查看过去 24h 的运营统计（对话数、接管次数、结案率、CSAT 均分）
2. **SLA breach 告警** — 当 SLA timer 超时（`sla_*` 前缀的 TIMER_EXPIRED 事件）时，自动向 admin 频道发送告警

需求来源：`docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.4。

## /review 命令

### 命令解析

`protocol/commands.py:28` — `_COMMAND_DEFS` 新增 `"review": []`（无参数命令）。

### Handler 逻辑

`server.py:262-303` — `/review` handler 在 `wire_bridge_callbacks` 的 `on_admin_command` 中处理：

1. 获取所有对话（`conv_manager._conversations.values()`）
2. 查询过去 24h 事件（`event_bus.query(since=yesterday)`）
3. 聚合 4 项统计：
   - `conv_count` — 对话总数
   - `takeover_count` — MODE_CHANGED → "takeover" 的事件数
   - `resolve_rate` — closed 对话占比（百分比）
   - `csat_avg` — 有 CSAT 评分对话的均分
4. 通过 `bridge_server.send_reply(conversation_id="__admin", visibility="system")` 返回格式化文本

### 输出格式

有数据时：
```
[review] 过去 24h 统计:
  对话数: 1
  接管次数: 1
  结案率: 100.0%
  CSAT 均分: 4.0
```

无数据时：
```
[review] 暂无统计数据（过去 24h 无对话）
```

## SLA breach 告警

### 触发条件

`server.py:490-511` — `_on_sla_breach` 订阅 `EventType.TIMER_EXPIRED`：

- 仅处理 `event.data["name"]` 以 `sla_` 开头的 timer（如 `sla_first_reply`、`sla_resolution`）
- 非 `sla_` 前缀的 timer（如 `idle_timeout`）直接跳过

### 告警行为

1. `bridge_server.send_event("sla.breach", {...}, target_capabilities={"operator", "admin"})` — 结构化 event
2. `bridge_server.send_reply(conversation_id="__admin", visibility="system")` — 可读文本告警

告警 event payload：
```json
{
  "conversation_id": "conv-sla-001",
  "breach_type": "sla_first_reply",
  "timeout_seconds": 30
}
```

告警文本：
```
[SLA 告警] conv_id=conv-sla-001 breach=sla_first_reply timeout=30s
```

### EventBus 订阅

`server.py:516` — `event_bus.subscribe(EventType.TIMER_EXPIRED, _on_sla_breach)`

## 行为预期

| # | 预期 | 状态 |
|---|------|------|
| 1 | `/review` 被 parse_command 正确解析（name=review, args={}） | CONFIRMED |
| 2 | 有对话时 /review 返回 4 项统计（对话数 / 接管次数 / 结案率 / CSAT 均分） | CONFIRMED |
| 3 | 无对话时 /review 返回 "暂无统计数据" | CONFIRMED |
| 4 | /review 回复 visibility=system，conversation_id=__admin | CONFIRMED |
| 5 | SLA timer 超时 → send_event("sla.breach") + send_reply 告警 | CONFIRMED |
| 6 | 告警包含 conversation_id + breach_type + timeout_seconds | CONFIRMED |
| 7 | 非 SLA timer（如 idle_timeout）不触发告警 | CONFIRMED |

## 风险

- **低风险**：/review 为只读统计查询，不修改任何状态。
- SLA 告警为事件驱动，不阻塞主流程。
- `csat_scores` 为空时除法保护（`if csat_scores else 0.0`）。
- `conv_count == 0` 时除法保护（`if conv_count > 0 else 0.0`）。

## 验证范围

- 覆盖：5 个 unit 测试（`tests/unit/test_review_command.py`），验证命令解析 + handler 统计 + SLA 告警。
- E2E：2 个 E2E 测试（`tests/e2e/test_sla_alerts.py`），验证 /review 全链路。
- 不覆盖：SLA timer 创建和调度（属于 TimerManager 模块，Task 4.6.7 覆盖）。
