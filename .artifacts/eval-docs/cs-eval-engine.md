---
type: eval-doc
id: cs-eval-engine
status: confirmed
producer: skill-5
created_at: "2026-04-14T00:00:00Z"
mode: simulate
feature: channel-server engine 模块 — 对话运行时
submitter: yaosh
related: []
---

# Eval: channel-server engine 模块 — 对话运行时

## 基本信息
- 模式：simulate
- 提交人：yaosh
- 日期：2026-04-14
- 状态：confirmed
- Spec 参考：`docs/discuss/spec/channel-server/02-channel-server.md §3`

## Feature 描述

engine/ 是 channel-server 有状态的运行时层，基于 protocol/ 原语构建：
- **EventBus** — 事件发布/订阅 + SQLite 持久化
- **ConversationManager** — 对话 CRUD + 生命周期状态机 + operator 并发上限
- **ModeManager** — 模式状态机转换 + MODE_CHANGED 事件
- **TimerManager** — asyncio 计时器（设置/取消/超时触发事件）
- **MessageStore** — 消息存储 + edit 链
- **PluginManager** — 插件目录扫描 + 钩子注册 + sync/async 调用
- **ParticipantRegistry** — IRC nick → role 映射
- **SquadRegistry** — agent ↔ operator 分队映射

## Testcase 表格

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 优先级 |
|---|------|---------|---------|---------|--------|
| 1 | EventBus 发布/订阅 | 无 | subscribe + publish | 订阅者收到 1 次 | P0 |
| 2 | EventBus 持久化 | SQLite | publish → 重启 → query | 事件跨进程可查 | P0 |
| 3 | EventBus 按类型查询 | 多种类型事件 | query(event_type=X) | 只返回匹配类型 | P0 |
| 4 | EventBus 异步订阅者 | async handler | publish + await | 被正确 await | P1 |
| 5 | EventBus 订阅者异常隔离 | 一个 handler 抛异常 | publish | 其他 handler 仍被调用 | P1 |
| 6 | ConversationManager CRUD | 无 | create/get | 幂等，未知返回 None | P0 |
| 7 | 生命周期状态机 | created | activate→idle→reactivate→close | 状态正确转移 | P0 |
| 8 | operator 并发上限 | max=2 | 添加到第 3 个会话 | 抛 ConcurrencyLimitExceeded | P0 |
| 9 | 非 operator 不受限 | max=2 | agent 加入 5 个会话 | 全部成功 | P0 |
| 10 | resolve + CSAT | active | resolve() 再 set_csat | state=closed, csat 存储 | P0 |
| 11 | 持久化跨重启 | 写入后析构 | 新实例 get | 状态+参与者+metadata 还原 | P0 |
| 12 | list_active 过滤 closed | 含 closed 对话 | 新实例 list_active | closed 不出现 | P0 |
| 13 | 非法状态转换 | CREATED | idle() | 抛 ValueError | P0 |
| 14 | ModeManager 合法转换 | ACTIVE auto | transition → COPILOT | mode="copilot" | P0 |
| 15 | ModeManager 非法转换 | auto | transition → auto | ValueError | P0 |
| 16 | ModeManager 发事件 | async | atransition | MODE_CHANGED 事件含 from/to/trigger | P0 |
| 17 | ModeManager takeover 链 | active | auto→copilot→takeover→auto | 每步都合法 | P1 |
| 18 | TimerManager 到期触发 | 0.1s | set_timer + await 0.3s | 1 个 TIMER_EXPIRED | P0 |
| 19 | TimerManager 取消 | 0.5s | set + cancel | 0 event | P0 |
| 20 | TimerManager 覆盖旧任务 | 0.5s 后设 0.1s | set + set | 仅 1 次触发 | P0 |
| 21 | TimerManager 未知 cancel | 空 | cancel 不存在 key | no-op 不抛 | P1 |
| 22 | MessageStore 保存+读取 | 无 | save + get | 字段一致 | P0 |
| 23 | MessageStore edit 链 | 有原消息 | edit | 新消息 edit_of 指向原 id | P0 |
| 24 | MessageStore 按会话查询 | 多会话消息 | query_by_conversation | 仅返回该会话 | P0 |
| 25 | PluginManager 加载目录 | 目录含插件 | load_hooks_from_dir | 钩子注册 | P0 |
| 26 | PluginManager async 钩子 | async on_mode_changed | call_async | 被 await | P0 |
| 27 | PluginManager 多插件累积 | a.py + b.py | load | 同名钩子并列 | P1 |
| 28 | PluginManager 跳过私有 | _private.py | load | 不加载 | P1 |
| 29 | ParticipantRegistry 注册+识别 | 无 | register_agent + identify | role 正确 | P0 |
| 30 | ParticipantRegistry bridge→customer | 注册 bridge | identify(bridge_nick) | role=CUSTOMER | P0 |
| 31 | ParticipantRegistry 冲突检测 | 已注册 agent | register_operator 同 nick | ValueError | P0 |
| 32 | SquadRegistry assign/get | 无 | assign + get_operator | 映射正确 | P0 |
| 33 | SquadRegistry reassign | 已分配 | reassign 到新 operator | 旧 squad 移除，新 squad 添加 | P0 |
| 34 | SquadRegistry 幂等 assign | 同 agent/operator | 再次 assign | squad 不重复 | P1 |
