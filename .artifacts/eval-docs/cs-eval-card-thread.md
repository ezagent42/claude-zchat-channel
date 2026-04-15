---
type: eval-doc
id: cs-eval-card-thread
status: confirmed
mode: verify
feature: "feishu_bridge card+thread 模型 + operator 自动 hijack"
producer: skill-5
submitter: yaosh
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-plan-card-thread
  - cs-diff-card-thread
  - cs-report-card-thread
---

# Eval: feishu_bridge card+thread 模型 + operator 自动 hijack

## 背景

Phase 4.5 的 feishu_bridge 使用 `send_text` 逐条发送 squad 群消息，缺乏对话聚合能力：
- operator 在 squad 群中看到的是散落的文本消息，无法一眼定位某个 conversation 的上下文
- 没有 card 状态展示（模式 / 进度 / 关闭），operator 需要人工跟踪对话状态
- 消息编辑无法映射回飞书（cs 侧 edit 不会同步到客户可见消息）
- 缺少 auto-hijack 检测：operator 在客户群内直接发言时，系统无法自动切换到人工接管模式

Task 4.6.5 将 squad 群消息模型从 "单条 send_text" 升级为 "interactive card (thread root) + thread reply"，
并新增 operator auto-hijack 检测机制。

需求来源：`docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.5。

## Before / After 架构

### Before（Phase 4.5）

```
conversation event
  └─ VisibilityRouter.route()
       ├─ public  → send_text(customer_chat) + send_text(squad_chat)
       ├─ side    → send_text(squad_chat)
       └─ system  → send_text(squad_chat) + send_text(admin_chat)
```

squad 群内消息平铺，无聚合、无状态卡片、无编辑映射。

### After（Task 4.6.5）

```
conversation.created
  └─ on_conversation_created() → send_card(squad) → card_msg_id 作为 thread root

conversation event
  └─ VisibilityRouter.route()
       ├─ public  → send_text(customer_chat) + reply_in_thread(card_msg_id)
       ├─ side    → reply_in_thread(card_msg_id)
       └─ system  → reply_in_thread(card_msg_id) + send_text(admin_chat)

edit event → on_edit() → msg_id_map 查找 → update_message(feishu_msg_id)
mode.changed → on_mode_changed() → update_card(card_msg_id, 新 mode)
conversation.closed → on_conversation_closed() → update_card(card_msg_id, state=closed)

operator 在 customer 群发言 → bridge._on_message()
  → is_operator_in_customer_chat() → _trigger_auto_hijack(callback)
```

## 行为预期

| # | 预期 | 状态 |
|---|------|------|
| 1 | `on_conversation_created()` 在 squad 群发送 interactive card 作为 thread root，返回 `card_msg_id` | CONFIRMED |
| 2 | card 包含 header（conv_id + 状态标签）、fields（模式/客户/结果）、操作按钮（接管/结案） | CONFIRMED |
| 3 | `route()` public 消息 → 双写：`send_text(customer_chat)` + `reply_in_thread(card_msg_id)` | CONFIRMED |
| 4 | `route()` side 消息 → 仅 `reply_in_thread(card_msg_id)`，不发送到 customer_chat | CONFIRMED |
| 5 | `route()` system 消息 → `reply_in_thread(card_msg_id)` + 可选 `send_text(admin_chat)` | CONFIRMED |
| 6 | public 消息带 `message_id` 时，建立 `cs_msg_id → feishu_msg_id` 映射 | CONFIRMED |
| 7 | `on_edit()` 通过 `_msg_id_map` 查找 feishu_msg_id → `update_message` + thread 追加 `[edited]` | CONFIRMED |
| 8 | `on_mode_changed()` 调用 `update_card` 刷新 card 的 mode 标签 | CONFIRMED |
| 9 | `on_conversation_closed()` 调用 `update_card` 标记 closed，注入 resolution / CSAT | CONFIRMED |
| 10 | `group_manager.is_operator_in_customer_chat()` 正确识别 operator 在动态 customer 群内发言 | CONFIRMED |
| 11 | `bridge._on_message()` 检测到 operator 在 customer 群发言 → 触发 `on_auto_hijack` 回调 | CONFIRMED |
| 12 | `on_auto_hijack` 回调异常被吞掉，不影响主消息处理 | CONFIRMED |
| 13 | `ConvThread` dataclass 正确跟踪 conversation_id / squad_chat_id / card_msg_id / mode / state | CONFIRMED |
| 14 | `sender.reply_in_thread_sync` / `update_card_sync` 新 API 正确封装 lark-oapi 调用 | CONFIRMED |

## 风险

- **无重大风险**：所有修改限于 `feishu_bridge/` 目录，不触碰 channel-server / engine / bridge_api。
- `VisibilityRouter.route` 行为变化：squad 侧由 `send_text` 改为 `reply_in_thread`，需先调用 `on_conversation_created` 建立 thread root。
- TC-10（live feishu E2E）缺真实凭证，在无凭证环境下跳过。

## 验证范围

- 覆盖：15 个 unit 测试（test_visibility_router.py 8 个 + test_auto_hijack.py 4 个 + test_group_manager.py 1 个 + test_visibility.py 2 个），验证 card+thread 路由、msg_id 映射、auto-hijack 检测。
- 不覆盖：TC-10 live feishu E2E（需真实 app_id/app_secret，待 staging 环境联调）。
