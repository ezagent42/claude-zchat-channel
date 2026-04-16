---
type: eval-doc
id: cs-eval-arch-review
status: open
mode: verify
feature: "架构审查 — 15 项 bug 修复"
producer: skill-5
submitter: yaosh
created_at: "2026-04-16"
updated_at: "2026-04-16"
related:
  - cs-plan-arch-review
  - cs-diff-arch-review
  - cs-report-arch-review
---

# Eval: 架构审查 Bug 修复

## CRITICAL

### C1: on_operator_message 回调未接线
- 位置: server.py wire_bridge_callbacks() L477-482
- bridge_api/ws_server.py L255 定义了 on_operator_message hook
- wire_bridge_callbacks 未接此 hook → operator 通过 Bridge API 发的消息被丢弃
- 修复: 新增 _on_operator_message callback，转发到 IRC 或处理 Gate visibility

### C2: Plugin 加载无报错
- 位置: plugins/manager.py L26, server.py L532
- plugins_dir 不存在时静默跳过，SLA timer 等功能静默失效
- 修复: 添加 warning log

### C3: Sync DB in async context
- 位置: server.py 各 async callback 中调用 ConversationManager 同步方法
- SQLite check_same_thread=False 允许跨线程，但 asyncio 单线程下不会真正并发
- 实际风险: asyncio 是协程式并发，SQLite 操作是瞬时 CPU-bound，不会被 await 中断
- 结论: 对 SQLite + asyncio 协程模型，当前实现是安全的。不需要 run_in_executor。
- 修复: 添加注释说明安全性理由

## HIGH

### H4: Event subscriber 异常处理
- 位置: engine/event_bus.py L49-50
- 已经有 log.error + exc_info=True，实际已包含完整 traceback
- 结论: 当前实现合理（事件总线设计就是单个订阅者失败不中断其他）
- 修复: 无需修改（已有 exc_info=True）

### H5: Operator 并发限制仅内存
- 位置: engine/conversation_manager.py L146-155
- _count_operator_active 从内存 dict 计数
- 重启后 _conversations 通过 _load_from_db() 重新加载所有非 CLOSED 对话
- 结论: 重启后内存数据从 DB 恢复，计数是准确的
- 修复: 无需修改

### H6: Event query timestamp 比较
- 位置: engine/event_bus.py L85-87
- timestamps 全部用 datetime.isoformat() 存储，SQLite 字符串比较对 ISO 8601 有效
- 结论: 只要所有时间都是 UTC 或本地（一致），排序正确
- 修复: 在 _persist 中确保使用 UTC

### H7: WAL checkpoint
- 位置: engine/db.py L10-14
- WAL 模式下 SQLite 自动 checkpoint（默认 1000 pages）
- 非优雅关闭时 WAL 文件会在下次打开时自动 recovery
- 修复: 在 server shutdown 时显式 checkpoint

## MEDIUM

### M8: Timer 替换竞态
- 位置: engine/timer_manager.py L32-34
- L63 已经检查 `self._tasks.get(key) is not asyncio.current_task()`
- 这个检查正确防止了被替换的旧 timer fire
- 结论: 已正确处理
- 修复: 无需修改

### M9: Mode transition 原子性
- 位置: protocol/mode.py L36-47, server.py L97
- mode_manager.atransition 内部先 validate 再设置 mode
- asyncio 协程是协作式调度，在 await 点之间不会被打断
- validate + set_mode 之间没有 await → 原子
- 修复: 无需修改

### M10: Bridge dedup 逻辑
- 位置: bridge_api/ws_server.py L176-195
- instance_id 是注册时客户端提供的唯一标识
- 旧连接在 finally 中被清理（L267），新连接覆盖 _connections dict
- 结论: 正常情况不会重复
- 修复: 无需修改

## LOW

### L11: Feishu sender 非 async
- 修复: 在 sender.py 中使用 asyncio.to_thread() 包装阻塞调用

### L12: Command args 缺少参数校验
- 修复: parse_command 中当必需参数缺失时返回错误而非静默跳过

### L13-15: 其他
- L13: Visibility 硬编码 — 当前够用，不改
- L14: 缺组件检查 — 添加 startup log
- L15: chunk_message 效率 — 不影响功能，不改
