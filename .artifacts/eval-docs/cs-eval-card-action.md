# Eval-doc: 飞书卡片回调 — CardAwareClient + CSAT 评分闭环

| 字段 | 值 |
|------|-----|
| ID | cs-eval-card-action |
| 类型 | eval-doc |
| 模式 | simulate |
| 状态 | confirmed |
| 产出者 | skill-5 |
| 消费者 | skill-2 |
| 创建时间 | 2026-04-15T19:00:00Z |

## 背景

lark_oapi 1.5.3 的 WSS Client 收到 `MessageType.CARD` 帧后直接 `return`（`client.py:264`），
不调用任何 handler，也不写回 Response。CSAT 评分卡片点击事件被静默丢弃。

需要继承 `lark.ws.Client` 补上 CARD 帧分发，完成评分闭环：
卡片点击 → CardAwareClient 分发 → bridge 解析 score → Bridge API → channel-server set_csat()

## 行为预期

### BEH-1: CARD 帧分发到 card_handler

- **触发**: lark_oapi WSS client 收到 `MessageType.CARD` 帧
- **预期**: payload 被解析为 dict，传递给注册的 `card_handler(payload)`
- **验证**: card_handler 被调用且参数正确；Response(200) 写回 WebSocket

### BEH-2: card_handler 解析 action.value

- **触发**: card_handler 收到 payload
- **预期**: 从 `payload["action"]["value"]` 提取 `score` 和 `conv_id`
- **验证**: score 和 conv_id 解析为正确的字符串值

### BEH-3: Bridge API 发送 CSAT 消息

- **触发**: score 和 conv_id 解析成功
- **预期**: bridge 通过 Bridge API WebSocket 发送 `{"type": "customer_message", "conversation_id": conv_id, "csat_score": N}`
- **验证**: Bridge API server 的 `on_customer_message` 收到包含 csat_score 的消息

### BEH-4: channel-server set_csat 闭环

- **触发**: `_on_customer_message` 收到 csat_score 字段
- **预期**: `conv_manager.set_csat(conv_id, int(csat_score))` 被调用
- **验证**: conversation 的 resolution.csat_score 被正确设置

### BEH-5: 非 CARD 帧不受影响

- **触发**: EVENT / PING / PONG 等非 CARD 帧到达
- **预期**: 走原 SDK `_handle_data_frame` 逻辑，card_handler 不被调用
- **验证**: EVENT 帧仍然正确分发到 event_handler

### BEH-6: card_handler 异常不 crash 连接

- **触发**: card_handler 抛出任意异常
- **预期**: 异常被捕获，记录日志，Response(500) 写回，WSS 连接不断
- **验证**: 异常后连接仍然存活，后续帧仍可处理

### BEH-7: payload 缺少必需字段时静默忽略

- **触发**: payload 中缺少 `score` 或 `conv_id`（或 action.value 结构异常）
- **预期**: 不发送 Bridge API 消息，不报错，正常返回
- **验证**: 无 Bridge API 调用，无异常抛出

## 约束

- engine/ protocol/ bridge_api/ transport/ 0 改动
- 新增文件: `feishu_bridge/ws_client.py`
- 修改文件: `feishu_bridge/bridge.py` (start + _on_card_action)
- 回归: 全部已有测试继续 PASS

## 关联 Artifact

- test-plan: cs-plan-card-action
- code-diff: cs-diff-card-action
- e2e-report: cs-report-card-action
