---
type: code-diff
id: cs-diff-review-sla
status: confirmed
producer: skill-3
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-review-sla
  - cs-plan-review-sla
  - cs-report-review-sla
---

# Code Diff: Task 4.6.4 — /review 命令 + SLA breach 告警

## 来源

- plan: `cs-plan-review-sla`
- eval-doc: `cs-eval-review-sla`
- 背景：运营管理员需要 /review 查看 24h 统计 + SLA timer 超时自动告警。

## 变更文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `protocol/commands.py` | M | `_COMMAND_DEFS` 新增 `"review": []` (:28) |
| `server.py` | M | 新增 /review handler (:262-303) + `_on_sla_breach` handler (:490-511) + EventBus 订阅 (:516) |
| `tests/unit/test_review_command.py` | A | 5 个 unit 测试 |
| `tests/e2e/test_sla_alerts.py` | A | 2 个 E2E 测试 |

## 改动类型

### protocol/commands.py（Modified — 命令定义）

```python
_COMMAND_DEFS: dict[str, list[str]] = {
    ...
    "review": [],        # 新增：无参数命令
}
```

`parse_command("/review")` → `Command(name="review", args={}, raw="/review")`

### server.py — /review handler（:262-303）

在 `wire_bridge_callbacks` 的 `on_admin_command` 回调中新增 `cmd.name == "review"` 分支：

```python
if cmd.name == "review":
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    all_convs = list(conv_manager._conversations.values())
    conv_count = len(all_convs)
    all_events = event_bus.query(since=yesterday)
    takeover_count = sum(1 for e in all_events
        if e.type == EventType.MODE_CHANGED
        and e.data.get("to") == "takeover")
    resolved_count = sum(1 for c in all_convs if c.state.value == "closed")
    csat_scores = [c.resolution.csat_score for c in all_convs
        if c.resolution is not None and c.resolution.csat_score is not None]
    csat_avg = sum(csat_scores) / len(csat_scores) if csat_scores else 0.0
    resolve_rate = round(resolved_count / conv_count * 100, 1) if conv_count > 0 else 0.0
```

- 无数据：返回 `[review] 暂无统计数据（过去 24h 无对话）`
- 有数据：返回格式化统计文本（对话数 / 接管次数 / 结案率 / CSAT 均分）
- 通过 `send_reply(conversation_id="__admin", visibility="system")` 发送

### server.py — _on_sla_breach handler（:490-511）

```python
async def _on_sla_breach(event: Event) -> None:
    timer_name = event.data.get("name", "")
    if not timer_name.startswith("sla_"):
        return                                     # 非 SLA timer → 跳过
    conv_id = event.conversation_id
    duration = event.data.get("action_params", {}).get("duration_s", "?")
    await bridge_server.send_event("sla.breach", {
        "conversation_id": conv_id,
        "breach_type": timer_name,
        "timeout_seconds": duration,
    }, conv_id, target_capabilities={"operator", "admin"})
    await bridge_server.send_reply(
        conversation_id="__admin",
        text=f"[SLA 告警] conv_id={conv_id} breach={timer_name} timeout={duration}s",
        visibility="system",
    )
```

### server.py — EventBus 订阅（:516）

```python
event_bus.subscribe(EventType.TIMER_EXPIRED, _on_sla_breach)
```

## 影响模块

- `protocol/commands.py` — 新增 review 命令定义
- `server.py` — /review handler + SLA breach handler
- 测试套件 — 新增 5 unit + 2 E2E

**零改动模块**：engine/ transport/ bridge_api/ agent_mcp.py routing_config.py message.py

## 风险评估

- **低风险**：/review 为只读统计查询，不修改状态。
- SLA breach handler 通过 `timer_name.startswith("sla_")` 过滤，不影响非 SLA timer 的处理。
- 除法保护：`conv_count == 0` 和 `len(csat_scores) == 0` 时返回 0.0。
