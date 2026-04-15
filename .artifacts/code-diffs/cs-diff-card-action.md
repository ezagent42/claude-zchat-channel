# Code-diff: 飞书卡片回调 — CardAwareClient + CSAT 评分闭环

| 字段 | 值 |
|------|-----|
| ID | cs-diff-card-action |
| 类型 | code-diff |
| 状态 | confirmed |
| 产出者 | skill-3 |
| 消费者 | skill-4 |
| 创建时间 | 2026-04-15T19:15:00Z |

## 新增文件

### feishu_bridge/ws_client.py (~60 行)

- `CardAwareClient(lark.ws.Client)` — 继承 SDK Client，覆写 `_handle_data_frame`
- CARD 帧：解析 payload → 调用 `card_handler(payload)` → 写回 Response
- 非 CARD 帧：`super()._handle_data_frame(frame)` 透传
- 异常捕获：handler 异常 → Response(500) 写回，连接不断

### feishu_bridge/tests/test_card_action.py (6 unit tests)

- TC-1 ~ TC-6 覆盖 CardAwareClient 帧分发 + bridge _on_card_action 解析

### tests/e2e/test_csat_flow.py (2 E2E tests)

- TC-7, TC-8 覆盖端到端 CSAT 评分闭环

## 修改文件

### feishu_bridge/bridge.py

- import `CardAwareClient`
- `__init__`: 新增 `self._bridge_ws` 属性
- 新增 `_parse_card_action(payload)` 静态方法 — 解析 action.value
- 新增 `_on_card_action(payload)` — 解析 + 发送到 Bridge API
- `start()`: `lark.ws.Client` → `CardAwareClient`，传入 `card_handler=self._on_card_action`

## 0 改动文件

- engine/ protocol/ bridge_api/ transport/ — 全部未修改

## 关联 Artifact

- eval-doc: cs-eval-card-action
- test-plan: cs-plan-card-action
- e2e-report: cs-report-card-action
