---
type: eval-doc
id: cs-eval-db-consolidation
status: confirmed
producer: skill-5
created_at: "2026-04-15"
mode: simulate
feature: "SQLite 数据库合并 — 3 文件 → 1 文件 5 表 + FK + CASCADE"
submitter: "yaosh"
related:
  - cs-plan-db-consolidation
  - cs-diff-db-consolidation
  - cs-report-db-consolidation
---

# Eval: SQLite 数据库合并 — 3 文件 → 1 文件 5 表 + FK + CASCADE

## 基本信息
- 模式：模拟
- 提交人：yaosh
- 日期：2026-04-15
- 状态：confirmed

## 架构适配分析

当前 3 个 engine 组件各持独立 SQLite 文件：
- ConversationManager → conversations.db (conversations, participants, resolutions)
- EventBus → conversations_events.db (events)
- MessageStore → conversations_messages.db (messages)

所有表通过 `conversation_id` 强关联，但跨文件无法建外键。

**问题：**
1. 孤儿数据 — 删对话后 events/messages 永久残留
2. 事务不一致 — resolve() + event_bus.publish() 跨文件，crash 后状态分裂
3. 无 FK 约束 — participants/resolutions 也没有 FK
4. edit 链断裂 — messages.edit_of 引用同表 message ID 但无 FK

**方案：** 新建 `engine/db.py` 统一初始化，1 个 `conversations.db` 包含 5 张表 + FK + CASCADE。
3 个组件构造函数改为接收 `conn: sqlite3.Connection`。

**影响范围：** engine/ + server.py + tests/。protocol/ bridge_api/ transport/ feishu_bridge/ 零改动。

## Testcase 表格

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 模拟效果 | 差异 | 优先级 |
|---|------|---------|---------|---------|---------|------|--------|
| 1 | init_db 创建全部 5 表 | 空数据库路径 | 调用 init_db(path) | conversations/participants/resolutions/events/messages 5 张表全部存在 | sqlite3_master 查询 5 张表 | 无 | P0 |
| 2 | PRAGMA foreign_keys 生效 | init_db() 返回连接 | PRAGMA foreign_keys 查询 | 返回 1 | 直接查询 PRAGMA | 无 | P0 |
| 3 | CASCADE 删除 participants | conversation 存在 + 有 participants | DELETE conversation | participants 自动删除 | FK CASCADE 触发 | 无 | P0 |
| 4 | CASCADE 删除 resolutions | conversation 存在 + 有 resolution | DELETE conversation | resolution 自动删除 | FK CASCADE 触发 | 无 | P0 |
| 5 | CASCADE 删除 messages | conversation 存在 + 有 messages | DELETE conversation | messages 自动删除 | FK CASCADE 触发 | 无 | P0 |
| 6 | events SET NULL on delete | conversation 存在 + 有 events | DELETE conversation | events.conversation_id 变 NULL（事件保留） | FK SET NULL 触发 | 无 | P0 |
| 7 | edit_of SET NULL | 原消息存在 + 编辑版本引用 | DELETE 原消息 | 编辑版本 edit_of 变 NULL | FK SET NULL 触发 | 无 | P0 |
| 8 | 3 组件共享连接 | init_db() 单连接 | 3 组件通过同一 conn 读写 | 互相可见对方写入 | 同一 sqlite3.Connection | 无 | P0 |
| 9 | FK 阻止无效 conversation_id | 无对应 conversation | INSERT participant 引用不存在的 conv_id | IntegrityError 拒绝 | FK 约束生效 | 无 | P1 |
| 10 | 全生命周期 E2E | 空数据库 | create → message → resolve → close | 全链路通过，所有表数据一致 | 完整生命周期 | 无 | P0 |
| 11 | 回归 240+ tests | 现有测试全部通过 | 运行全量测试 | 0 failed | 改造 fixture 后回归 | 无 | P0 |
