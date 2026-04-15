---
type: e2e-report
id: cs-report-feishu
status: pass
producer: skill-4
created_at: "2026-04-15"
related:
  - cs-plan-feishu
  - cs-eval-feishu
  - cs-diff-feishu
---

# Test Report: 飞书 Bridge

## 执行摘要

| 指标 | 值 |
|------|-----|
| 执行时间 | 2026-04-15 |
| 总测试数 | 139 |
| 新增测试 | 22 |
| 原有测试 | 117 |
| PASS | 139 |
| FAIL | 0 |
| SKIP | 0 |
| ERROR | 0 |
| 耗时 | ~16s |

## 飞书 Bridge 测试结果（22 tests）

### test_parsers.py（8 tests）

| 测试 | 状态 | 覆盖 |
|------|------|------|
| test_parse_text | PASS | text 消息解析 |
| test_parse_post | PASS | post 富文本解析 |
| test_parse_image_without_bridge | PASS | image fallback（无 bridge） |
| test_parse_interactive_card | PASS | interactive card 文本提取 |
| test_parse_sticker | PASS | sticker 标签 |
| test_parse_unknown_type | PASS | 未知类型 fallback |
| test_parse_location | PASS | location 位置消息 |
| test_parse_system | PASS | system 消息模板 |

### test_group_manager.py（9 tests）

| 测试 | 状态 | 覆盖 |
|------|------|------|
| test_admin_group | PASS | admin 角色识别 |
| test_squad_group | PASS | operator 角色 + operator_id |
| test_unknown_group_is_unknown_before_registration | PASS | unknown 返回 |
| test_bot_added_registers_as_customer | PASS | 动态 customer 注册 |
| test_customer_chats_persisted_and_loaded | PASS | JSON 持久化 + 重载 |
| test_bot_added_to_squad_group_skipped | PASS | 不覆盖已配置群 |
| test_member_added_to_admin_group | PASS | 成员加入 admin 群 |
| test_member_removed_from_squad | PASS | 成员退出 squad 群 |
| test_group_disbanded_removes_customer | PASS | 群解散清理 |

### test_sender.py（3 tests）

| 测试 | 状态 | 覆盖 |
|------|------|------|
| test_send_text_calls_api | PASS | send_text → create API |
| test_send_card_calls_api | PASS | send_card → create (interactive) |
| test_update_message_calls_patch_api | PASS | update → patch API |

### test_visibility.py（2 tests）

| 测试 | 状态 | 覆盖 |
|------|------|------|
| test_public_goes_to_customer_and_squad | PASS | public → 双群 |
| test_side_only_goes_to_squad | PASS | side → 仅 squad |

## 回归验证

原有 117 条 tests/unit/ 测试全部 PASS，无回归。

## 结论

Phase 4.5 飞书 Bridge 全部 22 个新增测试通过，0 FAIL 0 SKIP。
原有 117 条单元测试无回归。总计 139 PASS。
