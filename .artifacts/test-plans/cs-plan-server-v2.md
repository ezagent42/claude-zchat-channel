---
id: cs-plan-server-v2
type: test-plan
status: confirmed
phase: "Phase 4 补全: mode switching + gate enforcement E2E"
created_at: "2026-04-15"
related_ids:
  - cs-eval-server-v2
  - cs-diff-server-v2
producer: skill-2
triggers:
  - cs-eval-server-v2
  - cs-report-server (缺口标注：缺 mode switching + gate enforcement)
---

# cs-plan-server-v2 — Phase 4 补全测试计划

## 触发原因

`cs-report-server` 报告 Phase 4 E2E 4 PASS，但 `cs-eval-server-v2` 分析发现
`bridge_api` 缺少 `operator_join` 处理、`send_event()` 广播方法、以及
`server.py` 未注入 `on_operator_command` 回调——导致 mode switching 和
gate enforcement 路径完全未被 E2E 验证。本计划补全这两个缺口。

## 输入 Artifact

| 来源 | ID | 状态 |
|------|----|------|
| eval-doc | cs-eval-server-v2 | confirmed |
| 已有报告 | cs-report-server | pass（基线） |
| code-diff | cs-diff-server-v2 | 待实现后生成 |

## 改动影响范围（基于 eval-doc 分析）

| 组件 | 改动类型 | 风险 |
|------|---------|------|
| `bridge_api/ws_server.py` | 新增 `on_operator_join` 槽 + handler；新增 `send_event()` | 中（扩展，无破坏性） |
| `server.py main()` | 新增注入 `on_operator_join` + `on_operator_command` 回调 | 中（核心路径延伸） |
| 现有 8 个 unit bridge_api 测试 | 回归风险：`_handle_connection` 扩展 | 低（handler 分支新增，不改现有路径） |
| 现有 4 条 E2E | 回归风险：channel-server 主循环新增回调 | 低（回调注入不影响非 operator 流量） |

## 测试用例列表

### E2E 层（新增 4 条）

| TC-ID | 测试文件 | 场景 | 来源 | 优先级 | 前置条件 | 操作步骤 | 预期结果 |
|-------|---------|------|------|--------|---------|---------|---------|
| TC-E05 | `test_mode_switching.py::test_operator_join_triggers_copilot` | operator_join → auto→copilot 模式切换 | cs-eval-server-v2 TC-M01 | P0 | ergo+server 运行；conversation 已创建（auto 模式） | bridge_ws 发 `customer_connect`；发 `operator_join` | Bridge 收到 `{type:"event", event_type:"mode.changed", data:{from:"auto", to:"copilot"}, conversation_id:...}` |
| TC-E06 | `test_mode_switching.py::test_hijack_triggers_takeover` | /hijack → copilot→takeover 模式切换 | cs-eval-server-v2 TC-M02 | P0 | TC-E05 完成；conversation 在 copilot 模式 | 发 `operator_command {command:"/hijack"}`；等待事件 | Bridge 收到 `mode.changed, to:"takeover"` |
| TC-E07 | `test_gate_enforcement.py::test_side_message_not_received_by_customer` | side 消息不到 customer bridge | cs-eval-server-v2 TC-G01 | P0 | 两个 WS：customer_ws（caps=["customer"]）+ operator_ws（caps=["operator"]）；conversation 在 takeover 模式（经 /hijack） | /hijack 后 server 发 `send_reply(conv_id, "...", "side")`（通过 /hijack 后的 side 系统通知触发）；等待两侧接收 | operator_ws 收到消息；customer_ws 不收到（timeout 5s） |
| TC-E08 | `test_gate_enforcement.py::test_mode_changed_event_broadcast_to_all` | mode.changed 事件广播到所有 Bridge（包括 customer side） | cs-eval-server-v2 TC-G02（改） | P1 | 两个 WS；conversation auto 模式 | operator_join → copilot 切换 | **两个** WS 都收到 mode.changed 事件（events 无 visibility 过滤） |

### Unit 层（新增 4 条）

| TC-ID | 测试文件 | 场景 | 来源 | 优先级 |
|-------|---------|------|------|--------|
| TC-U12 | `test_bridge_api.py`（新增） | `on_operator_join` 回调被调用 | code-diff 新增 handler | P0 |
| TC-U13 | `test_bridge_api.py`（新增） | `send_event()` 广播到所有连接 | code-diff 新增方法 | P0 |
| TC-U14 | `test_bridge_api.py`（新增） | `send_event()` 在无连接时不报错 | 边界 | P1 |
| TC-U15 | `test_server_integration.py`（新增） | `build_components()` 注入了 `on_operator_join` + `on_operator_command` 回调 | code-diff 注入 | P0 |

## 统计

| 层级 | 新增 | 覆盖来源 |
|------|------|---------|
| Unit | 4 | code-diff (新增组件) |
| E2E | 4 | eval-doc TC-M01/M02/G01/G02 |
| **合计** | **8** | — |

新增后总测试数：**125**（113 unit + 12 E2E）

## 风险标注

- **高风险**：`_handle_connection` 扩展（`operator_join` 分支）必须不破坏现有 `customer_connect / register / operator_command` 分支
- **回归风险**：`server.py` 注入回调后，原有 4 条 E2E（registration / customer_connect / startup）必须仍然 PASS
- **新风险**：`send_event()` 对断连的 websocket 发送时异常处理（需 try/except，已在设计中标注）

## 测试执行方式

```bash
cd zchat-channel-server
uv run pytest tests/unit/ -v                          # 应 PASS 117 条（113 + 4）
uv run pytest tests/e2e/ -v -m e2e --timeout=30      # 应 PASS 8 条（4 原 + 4 新）
```

## 退出标准

- 125 条测试 0 FAIL 0 SKIP
- `.artifacts/e2e-reports/cs-report-server-v2.md` 落地，status=pass
