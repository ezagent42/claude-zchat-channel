---
type: code-diff
id: cs-diff-engine
status: implemented
producer: skill-6
consumers: [skill-4]
created_at: "2026-04-14T00:00:00Z"
related:
  - eval-doc: cs-eval-engine
  - test-plan: cs-plan-engine
branch: feat/engine
submodule: zchat-channel-server
---

# Code Diff: channel-server engine 模块

## 新增文件

### engine/__init__.py
包声明。

### engine/event_bus.py (~125 行)
- `EventBus(db_path)` — SQLite 持久化 + 内存订阅者表
- `subscribe(event_type, cb)` — 支持 sync/async 回调
- `publish(event)` — 先落盘，再通知订阅者；单个订阅者异常不中断其他
- `query(conversation_id=, event_type=, since=)` — 历史事件查询
- `close()` — 关闭数据库连接

### engine/conversation_manager.py (~250 行)
- `ConversationManager(db_path, max_operator_concurrent=5)` — 启动时从 SQLite 加载非 closed 对话
- `create(id, metadata=)` — 幂等创建
- `get(id)` — 内存优先，未命中懒加载（支持查询 closed）
- `activate/idle/reactivate/close` — 调用 `protocol.transition_state` 做合法性校验
- `add_participant(conv_id, p)` — operator 超限抛 `ConcurrencyLimitExceeded`
- `remove_participant(conv_id, pid)`
- `resolve(conv_id, outcome, resolved_by)` — 写 resolutions 表 + close
- `set_csat(conv_id, score)` — 1..5 校验
- `list_active()`
- 三张表：`conversations` / `participants` / `resolutions`

### engine/mode_manager.py (~60 行)
- `transition(conv, new_mode, trigger, by)` — 同步版，调用 `protocol.validate_transition`
- `atransition(...)` — 异步版，额外发出 `MODE_CHANGED` 事件

### engine/timer_manager.py (~65 行)
- `set_timer(conv_id, name, duration, on_expire)` — asyncio.create_task；同 key 重设先 cancel
- `cancel_timer(conv_id, name)` — 未知为 no-op
- `_wait_and_fire(timer)` — sleep 后发 `TIMER_EXPIRED`，处理 CancelledError 和被覆盖的场景

### engine/message_store.py (~90 行)
- `save(Message)` — upsert
- `get(id)` — 返回 Message 或 None
- `edit(original_id, new_content)` — 生成新 Message，`edit_of` 指向原 id（保留原消息）
- `query_by_conversation(conv_id)` — 按 timestamp 升序

### engine/plugin_manager.py (~55 行)
- `load_hooks_from_dir()` — 目录扫描 `.py`（跳过 `_*`），导入模块，自动注册 `on_*` 函数
- `hooks(name)` — 返回列表副本
- `call_async(name, *args)` — 调用所有同名钩子，sync/async 混合

### engine/participant_registry.py (~55 行)
- `register_agent/register_operator/register_bridge` — 角色冲突抛 ValueError
- `identify(nick)` — agent → operator → bridge (返回 CUSTOMER) → None
- `unregister(nick)`

### engine/squad_registry.py (~40 行)
- `assign(agent_id, operator_id)` — 若已属其他 operator，先 detach 再重新 attach
- `reassign/unassign/get_operator/get_squad`

## 修改文件

### protocol/event.py
- `Event.conversation_id` 改为默认 `""`
- `Event.data` 改为默认 `field(default_factory=dict)`

**理由：** engine 层的 API 需要构造生命周期事件（如 `CONVERSATION_CREATED`）时无自定义 payload，沿用 Event 原语，这两个默认值既向后兼容又让调用更简洁。

## 依赖链

engine/ 全部模块仅依赖 `protocol/`（已在 feat/protocol 提供）+ 标准库 + `pytest-asyncio`。不依赖 IRC / MCP / WebSocket。

## 提交序列（feat/engine 分支）

1. `feat(engine): EventBus — pub/sub + SQLite persistence (6 tests)`
2. `feat(engine): ConversationManager — CRUD + state machine + concurrency limit (13 tests)`
3. `feat(engine): ModeManager + TimerManager — transitions & async timers (9 tests)`
4. `feat(engine): message_store + plugin_manager + registries (26 tests)`
