---
type: code-diff
id: cs-diff-card-thread
status: draft
producer: skill-3
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-feishu
  - cs-plan-feishu
  - cs-diff-feishu
---

# Code Diff: Task 4.6.5 — feishu_bridge card+thread 模型 + auto-hijack

> 在 Phase 4.5 已有 feishu_bridge 基础上，将 squad 群消息从单条 send_text 升级为
> "interactive card + thread reply" 模型；同时支持客户消息编辑映射（cs↔feishu msg_id）
> 及 operator 在客户群发言自动 hijack 检测。

## 修改文件

### `feishu_bridge/visibility_router.py`（重写）

- 新增 `ConvThread` dataclass：跟踪 conversation_id → squad_chat_id / card_msg_id /
  customer_chat_id / mode / state / metadata。
- 新增 `_threads: dict[str, ConvThread]`、`_msg_id_map: dict[str, str]` 内部状态。
- 新增 `on_conversation_created(conv_id, metadata)`：在 squad 群发 interactive card
  作为 thread root，存入 `_threads`。
- 重写 `route(conv_id, message)`：
  - public → `send_text(customer_chat)` + `reply_in_thread(card_msg_id)`
  - side   → `reply_in_thread(card_msg_id)`
  - system → `reply_in_thread(card_msg_id)` + 可选 `send_text(admin_chat)`
  - 携带 `message_id` 时建立 `cs_msg_id → feishu_msg_id` 映射（仅 public 客户可见消息）
- 新增 `on_edit(conv_id, cs_msg_id, text)`：查映射 → `update_message`，并在 thread
  追加 `[edited]` 标记。
- 新增 `on_mode_changed(conv_id, mode)`：`update_card` 刷新模式标签。
- 新增 `on_conversation_closed(conv_id, resolution)`：`update_card` 标记关闭并
  注入 outcome / CSAT。
- 新增 `_build_conv_card(...)`：根据 mode/state/metadata 生成卡片
  （header 模板 blue/red、操作按钮、客户/模式/结果 fields）。

### `feishu_bridge/sender.py`

- 新增 `reply_in_thread_sync` / `reply_in_thread`（基于 `im.v1.message.reply` API，
  设置 `reply_in_thread=True`）。
- 新增 `update_card_sync` / `update_card`（沿用 `PatchMessageRequest`，content 为卡片 dict）。
- 新增 `update_message`、`send_card` 别名（与原有 `*_sync` 同步对齐风格）。
- import 新增 `ReplyMessageRequest`、`ReplyMessageRequestBody`。

### `feishu_bridge/group_manager.py`

- 新增 `is_operator(user_id)`：跨所有 squad 群判断是否为 operator。
- 新增 `is_operator_in_customer_chat(user_id, chat_id)`：仅当 chat 是动态 customer
  群且 user 是已知 operator 时返回 True。auto-hijack 触发判定的核心方法。

### `feishu_bridge/bridge.py`

- 新增 `on_auto_hijack: Callable[[str, str, str], Any] | None` 钩子（app 层注入）。
- `_on_message`：识别 customer 群且发送者为 operator → 调用 `_trigger_auto_hijack`。
- 新增 `_trigger_auto_hijack(conv_id, operator_id, text)`：调用回调，吞掉异常防止
  影响主消息处理流程。

## 新增测试

| 文件 | 测试数 | 覆盖 |
|------|--------|------|
| `feishu_bridge/tests/test_visibility_router.py` | 8 | TC-1 ~ TC-7 + 边界 |
| `feishu_bridge/tests/test_auto_hijack.py` | 4 | TC-9 + 反例 + 异常吞掉 |
| `feishu_bridge/tests/test_group_manager.py`（扩展） | +1 (TC-8) | `is_operator_in_customer_chat` |
| `feishu_bridge/tests/test_visibility.py`（重写） | 2 (TC-018/TC-019) | 适配 thread 模型 |

## 验证

- 本地全套 unit：`193 passed`（158 channel-server + 35 feishu_bridge）。
- E2E `test_card_thread_e2e`（TC-10）需真实飞书凭证，在缺凭证环境下跳过。
- TC-9（`test_auto_hijack_flow`）以 mock P2ImMessageReceiveV1 事件单测覆盖
  detection 逻辑，避免依赖 feishu live API。

## 影响范围

- 修改文件均位于 `feishu_bridge/`，不触碰 channel-server / engine / bridge_api。
- `VisibilityRouter.route` 行为变化：squad 侧由 `send_text` 改为 `reply_in_thread`，
  且需要先调用 `on_conversation_created` 建立 thread root，否则 squad 端不会收到消息。
- 现有 `tests/test_visibility.py` 已同步更新到新模型，无外部回归点。
