---
type: code-diff
id: cs-diff-irc-protocol
status: confirmed
producer: skill-3
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-irc-protocol
  - cs-plan-irc-protocol
  - cs-report-irc-protocol
---

# Code Diff: Task 4.6.2 — IRC 消息协议

## 来源

- plan: `cs-plan-irc-protocol`
- eval-doc: `cs-eval-irc-protocol`
- 背景：agent_mcp.py 和 channel-server 之间通过 IRC PRIVMSG 传递消息，需定义结构化前缀协议以区分消息类型。

## 变更文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `transport/irc_transport.py` | M | 新增 `parse_agent_message()` 函数（:30-62），解析 `__edit:` / `__side:` / `__msg:` 前缀 |
| `agent_mcp.py` | M | `_handle_reply()` (:200-219) 生成前缀 IRC 消息；`_handle_send_side_message()` (:240-250) 使用 `__side:` 前缀 |
| `tests/unit/test_irc_message_protocol.py` | A | 10 个 unit 测试覆盖双端协议 |
| `tests/e2e/test_message_protocol.py` | A | 2 个 E2E 测试覆盖 agent → channel-server → Bridge API 全链路 |

## 改动类型

### transport/irc_transport.py（Modified — 新增解析函数）

**新增 `parse_agent_message(text: str) -> dict`（:30-62）**:
- `__edit:<msg_id>:<text>` → `{"type": "edit", "message_id": msg_id, "text": text}`
- `__side:<text>` → `{"type": "side", "text": text}`
- `__msg:<msg_id>:<text>` → `{"type": "reply", "message_id": msg_id, "text": text}`
- 无前缀 → `{"type": "reply", "text": text}`
- 边界处理：`__edit:` / `__msg:` 后无冒号分隔 → fallback 为普通消息

### agent_mcp.py（Modified — 前缀生成逻辑）

**`_handle_reply()` (:200-219)** — 根据参数决定 IRC 前缀：
```python
if edit_of:
    prefixed = f"__edit:{edit_of}:{text}"       # 编辑替换
elif side:
    prefixed = f"__side:{text}"                  # side channel
else:
    prefixed = f"__msg:{message_id}:{text}"      # 普通回复
```
- 每次生成 `uuid.uuid4()` 作为 `message_id`
- 返回 JSON `{"message_id": "<uuid>", "sent_to": "<chat_id>"}`
- 通过 `chunk_message()` 分片发送

**`_handle_send_side_message()` (:240-250)** — 独立 tool，始终用 `__side:` 前缀：
```python
prefixed = f"__side:{text}"
```

### reply Tool Schema（agent_mcp.py :107-137）

Tool `reply` 的 inputSchema 新增两个可选字段：
- `edit_of: string` — 需编辑的原消息 message_id
- `side: boolean` — 是否发送为 side-channel 消息

## 影响模块

- `transport/irc_transport.py` — 新增 parse 函数
- `agent_mcp.py` — reply tool 前缀生成
- 测试套件 — 新增 10 unit + 2 E2E

**零改动模块**：engine/ protocol/ bridge_api/ server.py message.py

## 风险评估

- **低风险**：前缀协议为纯字符串约定，不影响 engine 或 Bridge API 行为。
- 所有前缀解析为纯函数（无 I/O、无状态），易于测试和验证。
