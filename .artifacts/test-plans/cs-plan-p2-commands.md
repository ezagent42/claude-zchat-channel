---
type: test-plan
id: cs-plan-p2-commands
status: confirmed
producer: skill-2
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-p2-commands
  - cs-diff-p2-commands
---

# Test Plan: P2 命令 handler (/abandon /assign /reassign /squad)

## 来源

- eval-doc: `cs-eval-p2-commands`
- code 改动范围：`server.py:_on_operator_command` + `_on_admin_command`，新增 SquadRegistry 接入 + `list_all`

## 用例列表

### Unit tests (`tests/unit/test_p2_commands.py`)

| ID | 名称 | 优先级 | 来源 | 验证 |
|----|------|--------|------|------|
| TC-001 | test_abandon_closes_active_conversation | P0 | eval TC-1 | `/abandon` → conv.state=closed |
| TC-002 | test_abandon_does_not_send_csat | P0 | eval TC-2 | 无 send_card 调用（无 csat_request 卡片） |
| TC-003 | test_abandon_emits_conversation_closed_event | P0 | eval TC-1 | event_bus 收到 conversation.closed |
| TC-004 | test_assign_creates_new_squad | P1 | eval TC-3 | SquadRegistry.get_operator + send_event(squad.assigned) |
| TC-005 | test_assign_overrides_existing | P1 | eval TC-4 | 第二次 assign 覆盖原 operator |
| TC-006 | test_reassign_explicit_migration | P1 | eval TC-5 | reassign 后 operator 切换 + squad.reassigned event |
| TC-007 | test_squad_list_all_operators | P2 | eval TC-6 | reply 文本含每个 operator 及其 agents |
| TC-008 | test_squad_list_single_operator | P2 | eval TC-7 | 指定 op1 时仅列 op1 的 agents |
| TC-009 | test_squad_empty_returns_message | P2 | eval TC-8 | 无分队时返回 "[squad] 暂无分队" |
| TC-010 | test_squad_registry_list_all | P2 | 工具方法 | SquadRegistry.list_all() 返回 {op_id: [agent_ids]} |

### E2E tests (`tests/e2e/test_p2_commands.py`)

| ID | 名称 | 优先级 | 验证 |
|----|------|--------|------|
| TC-E01 | test_abandon_e2e_flow | P0 | customer_connect → operator_command(/abandon) → 收到 conversation.closed event + system reply |
| TC-E02 | test_assign_then_squad_e2e | P1 | admin /assign + /squad → reply 含分队信息 |

## 统计

- 总数：10 unit + 2 E2E = 12
- P0: 3 (TC-001~003) + 1 E2E = 4
- P1: 3 (TC-004~006) + 1 E2E = 4
- P2: 4 (TC-007~010) = 4

## 风险

- E2E 端口竞争（已有 fixture 模式覆盖）。
- SquadRegistry 状态在多 fixture 共享时需验证隔离性 → 用 components 重建保障。

## 实现要点

1. `SquadRegistry.list_all()` → 返回 dict 拷贝。
2. `_on_operator_command` 增加 `cmd.name == "abandon"` 分支。
3. `_on_admin_command` 增加 `assign` / `reassign` / `squad` 分支，需要 components 注入 `squad_registry`。
4. `build_components()` 增加 `squad_registry` 实例。
