---
type: test-plan
id: cs-plan-irc-protocol
status: executed
producer: skill-2
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-irc-protocol
  - cs-diff-irc-protocol
---

# Test Plan: Task 4.6.2 — IRC 消息协议

## 来源

- eval-doc: `cs-eval-irc-protocol`
- plan: `docs/discuss/plan/06-phase4.6-architecture-split.md` Task 4.6.2
- code 改动范围：`transport/irc_transport.py`（`parse_agent_message()`）+ `agent_mcp.py`（`_handle_reply()` / `_handle_send_side_message()` 前缀生成）

## 用例列表

### Unit tests (`tests/unit/test_irc_message_protocol.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-001 | test_reply_returns_message_id | P0 | eval #3 | reply() 返回非空 UUID message_id |
| TC-002 | test_reply_edit_irc_prefix | P0 | eval #1 | reply(edit_of='msg_001') 生成 `__edit:msg_001:text` |
| TC-003 | test_reply_side_irc_prefix | P0 | eval #2 | reply(side=True) 生成 `__side:text` |
| TC-004 | test_cs_parse_edit_prefix | P0 | eval #4 | `__edit:msg_001:text` → type=edit, message_id=msg_001 |
| TC-005 | test_cs_parse_side_prefix | P0 | eval #5 | `__side:text` → type=side |
| TC-006 | test_cs_parse_no_prefix | P0 | eval #7 | 普通消息 → type=reply，无 message_id |
| TC-007 | test_cs_parse_msg_prefix | P0 | eval #6 | `__msg:uuid:text` → type=reply, message_id=uuid |
| TC-008 | test_cs_parse_edit_no_colon_fallback | P1 | eval #8 | `__edit:malformed`（无第二个冒号）→ fallback 普通消息 |
| TC-009 | test_cs_parse_edit_with_colons_in_text | P1 | eval #9 | 文本含多个冒号只按第一个分割 |
| TC-010 | test_reply_normal_uses_msg_prefix | P0 | eval #3 | 普通 reply 使用 `__msg:<uuid>:<text>` 前缀 |

### E2E tests (`tests/e2e/test_message_protocol.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-E01 | test_edit_e2e_flow | P0 | eval #4 | agent 发 `__edit:` → Bridge API 收到 type=edit |
| TC-E02 | test_side_e2e_flow | P0 | eval #5 | agent 发 `__side:` → Bridge API 收到 visibility=side |

## 统计

- 总数：10 unit + 2 E2E = 12
- P0: 10
- P1: 2

## 验证策略

1. **发送端验证**（agent_mcp）：通过 MagicMock 替代 IRC connection，调用 `_handle_reply()`，断言 `privmsg()` 参数包含正确前缀。
2. **接收端验证**（irc_transport）：直接调用 `parse_agent_message()` 纯函数，断言返回 dict 的 type / message_id / text 字段。
3. **E2E 验证**：启动 ergo + channel-server，模拟 agent IRC 连接发送前缀消息，通过 Bridge WebSocket 接收并验证路由结果。

## 风险

- E2E 测试依赖 ergo IRC server + channel-server 独立进程启动；unit 测试无外部依赖。
- `test_reply_returns_message_id` 需要导入 `agent_mcp` 模块（需要 `mcp` 库可用）。
