---
type: eval-doc
id: cs-eval-irc-protocol
status: confirmed
mode: verify
feature: "IRC 消息协议 — agent 前缀生成 + channel-server 前缀解析"
producer: skill-5
submitter: yaosh
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-plan-irc-protocol
  - cs-diff-irc-protocol
  - cs-report-irc-protocol
---

# Eval: Task 4.6.2 — IRC 消息协议

## 背景

Phase 4.6.1 完成架构拆分后，agent_mcp.py 和 channel-server 之间通过 IRC PRIVMSG
传递消息。需要定义结构化前缀协议，使 channel-server 能区分消息类型并路由到 Bridge API：

- `__msg:<uuid>:<text>` — 普通回复，携带 message_id（用于追踪和后续编辑）
- `__edit:<uuid>:<text>` — 编辑替换已有消息（覆盖原 message_id 内容）
- `__side:<text>` — side-channel 消息（visibility=side，仅 operator+admin 可见）
- 无前缀 — 普通消息，由 Gate 判定 visibility

需求来源：`docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.2。

## 双端协议

### agent_mcp.py（发送端）

`_handle_reply()` 根据参数组合生成 IRC 前缀：

```
reply(chat_id, text)                  → __msg:<uuid>:<text>
reply(chat_id, text, edit_of=id)      → __edit:<id>:<text>
reply(chat_id, text, side=True)       → __side:<text>
```

- `agent_mcp.py:200-219` — `_handle_reply()` 生成前缀逻辑
- `agent_mcp.py:240-250` — `_handle_send_side_message()` 使用 `__side:` 前缀

### transport/irc_transport.py（接收端）

`parse_agent_message()` 解析 IRC 文本，返回结构化 dict：

```python
parse_agent_message("__edit:msg_001:替换后的内容")
→ {"type": "edit", "message_id": "msg_001", "text": "替换后的内容"}

parse_agent_message("__side:建议内容")
→ {"type": "side", "text": "建议内容"}

parse_agent_message("__msg:uuid:你好")
→ {"type": "reply", "message_id": "uuid", "text": "你好"}

parse_agent_message("普通消息")
→ {"type": "reply", "text": "普通消息"}
```

- `transport/irc_transport.py:30-62` — `parse_agent_message()` 解析逻辑

## 行为预期

| # | 预期 | 状态 |
|---|------|------|
| 1 | `_handle_reply(edit_of='msg_001')` 发送 `__edit:msg_001:<text>` | CONFIRMED |
| 2 | `_handle_reply(side=True)` 发送 `__side:<text>` | CONFIRMED |
| 3 | `_handle_reply()` 普通消息发送 `__msg:<uuid>:<text>`，返回 JSON 含 message_id | CONFIRMED |
| 4 | `parse_agent_message("__edit:id:text")` 返回 `{type: "edit", message_id: id}` | CONFIRMED |
| 5 | `parse_agent_message("__side:text")` 返回 `{type: "side", text: text}` | CONFIRMED |
| 6 | `parse_agent_message("__msg:uuid:text")` 返回 `{type: "reply", message_id: uuid}` | CONFIRMED |
| 7 | `parse_agent_message("plain")` 返回 `{type: "reply", text: "plain"}`，无 message_id | CONFIRMED |
| 8 | `__edit:` 后无冒号 → fallback 为普通消息 | CONFIRMED |
| 9 | 文本中含冒号不影响解析（只按第一个冒号分割 msg_id） | CONFIRMED |

## 风险

- **低风险**：协议为纯字符串前缀约定，不涉及网络或状态。
- 文本中含冒号的边界情况已覆盖（`test_cs_parse_edit_with_colons_in_text`）。

## 验证范围

- 覆盖：9 个 unit 测试（`tests/unit/test_irc_message_protocol.py`），验证双端前缀生成 + 解析。
- E2E：2 个 E2E 测试（`tests/e2e/test_message_protocol.py`），验证 agent IRC → channel-server → Bridge API 全链路。
- 不覆盖：chunk_message 分片（属于 message.py 独立模块）。
