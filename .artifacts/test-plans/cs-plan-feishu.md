---
type: test-plan
id: cs-plan-feishu
status: confirmed
producer: skill-2
created_at: "2026-04-15"
trigger: "eval-doc cs-eval-feishu + spec 09-feishu-bridge.md — Phase 4.5 飞书 Bridge 新功能"
related:
  - cs-eval-feishu
---

# Test Plan: 飞书 Bridge 单元测试

## 触发原因

Phase 4.5 新增 `feishu_bridge/` 模块（8 个源文件），实现飞书 ↔ channel-server 协议转换。
eval-doc `cs-eval-feishu` 定义了 22 个 testcase，覆盖消息解析、群角色映射、visibility 路由、发送 API 四大功能区。

本计划将 eval-doc testcase 转换为可执行的 pytest 用例，分布在 4 个测试文件中。

## 用例列表

### TC-001: text 消息解析

- **来源**：eval-doc #1
- **优先级**：P0
- **前置条件**：`message_parsers` 模块可用
- **操作步骤**：
  1. 调用 `parse_message("text", {"text": "hello"}, None, None)`
- **预期结果**：返回 `("hello", "")`
- **涉及模块**：message_parsers
- **测试文件**：`test_parsers.py::test_parse_text`

### TC-002: post 富文本消息解析

- **来源**：eval-doc #2
- **优先级**：P0
- **前置条件**：`message_parsers` 模块可用
- **操作步骤**：
  1. 构造 post content（title + 二维 content 数组）
  2. 调用 `parse_message("post", content, None, None)`
- **预期结果**：返回文本包含 "标题"、"段落1"、"段落2"
- **涉及模块**：message_parsers
- **测试文件**：`test_parsers.py::test_parse_post`

### TC-003: image 消息（无 bridge）

- **来源**：eval-doc #3
- **优先级**：P0
- **前置条件**：bridge=None
- **操作步骤**：
  1. 调用 `parse_message("image", {"image_key": "img_xxx"}, None, None)`
- **预期结果**：返回描述性文本（含 "image"/"File"/"下载"）
- **涉及模块**：message_parsers
- **测试文件**：`test_parsers.py::test_parse_image_without_bridge`

### TC-004: interactive card 解析

- **来源**：eval-doc #4
- **优先级**：P0
- **前置条件**：card 含 header + elements
- **操作步骤**：
  1. 构造 interactive content（header.title + div elements）
  2. 调用 `parse_message("interactive", content, None, None)`
- **预期结果**：提取出 "评分" + "请评分" 文本
- **涉及模块**：message_parsers
- **测试文件**：`test_parsers.py::test_parse_interactive_card`

### TC-005: sticker 表情包

- **来源**：eval-doc #5
- **优先级**：P1
- **前置条件**：无
- **操作步骤**：
  1. 调用 `parse_message("sticker", {}, None, None)`
- **预期结果**：返回含 "表情" 的文本
- **涉及模块**：message_parsers
- **测试文件**：`test_parsers.py::test_parse_sticker`

### TC-006: unknown 类型 fallback

- **来源**：eval-doc #6
- **优先级**：P0
- **前置条件**：无
- **操作步骤**：
  1. 调用 `parse_message("some_future_type", {}, None, None)`
- **预期结果**：返回含 "some_future_type" 的描述文本
- **涉及模块**：message_parsers
- **测试文件**：`test_parsers.py::test_parse_unknown_type`

### TC-007: location 位置消息

- **来源**：eval-doc #7
- **优先级**：P1
- **前置条件**：无
- **操作步骤**：
  1. 调用 `parse_message("location", {"name": "星巴克", "latitude": "31.2", "longitude": "121.4"}, None, None)`
- **预期结果**：返回含 "星巴克" 的文本
- **涉及模块**：message_parsers
- **测试文件**：`test_parsers.py::test_parse_location`

### TC-008: system 消息

- **来源**：eval-doc #8
- **优先级**：P2
- **前置条件**：无
- **操作步骤**：
  1. 调用 `parse_message("system", {"template": "add_member"}, None, None)`
- **预期结果**：返回含 "加入" 或 "系统" 的文本
- **涉及模块**：message_parsers
- **测试文件**：`test_parsers.py::test_parse_system`

### TC-009: admin 群角色识别

- **来源**：eval-doc #9
- **优先级**：P0
- **前置条件**：GroupManager 实例化（admin_chat_id="oc_admin"）
- **操作步骤**：
  1. 调用 `gm.identify_role("oc_admin")`
- **预期结果**：返回 `"admin"`
- **涉及模块**：group_manager
- **测试文件**：`test_group_manager.py::test_admin_group`

### TC-010: squad 群角色识别 + operator_id

- **来源**：eval-doc #10
- **优先级**：P0
- **前置条件**：GroupManager 含 squad_chats 配置
- **操作步骤**：
  1. 调用 `gm.identify_role("oc_squad_1")`
  2. 调用 `gm.get_operator_id("oc_squad_1")`
- **预期结果**：返回 `"operator"` 和 `"xiaoli"`
- **涉及模块**：group_manager
- **测试文件**：`test_group_manager.py::test_squad_group`

### TC-011: 未知群返回 unknown

- **来源**：eval-doc #11
- **优先级**：P0
- **前置条件**：GroupManager 无 customer 注册
- **操作步骤**：
  1. 调用 `gm.identify_role("oc_random")`
- **预期结果**：返回 `"unknown"`
- **涉及模块**：group_manager
- **测试文件**：`test_group_manager.py::test_unknown_group_is_unknown_before_registration`

### TC-012: bot 拉入新群 → customer 注册

- **来源**：eval-doc #12
- **优先级**：P0
- **前置条件**：临时目录 + customer_chats_path
- **操作步骤**：
  1. 调用 `gm.register_customer_chat("oc_new")`
  2. 调用 `gm.identify_role("oc_new")`
- **预期结果**：返回 `"customer"`
- **涉及模块**：group_manager
- **测试文件**：`test_group_manager.py::test_bot_added_registers_as_customer`

### TC-013: customer 群持久化 + 重载

- **来源**：eval-doc #13
- **优先级**：P0
- **前置条件**：临时目录
- **操作步骤**：
  1. gm1 注册 customer → gm2 用同路径加载
  2. 调用 `gm2.identify_role("oc_persist")`
- **预期结果**：返回 `"customer"`
- **涉及模块**：group_manager
- **测试文件**：`test_group_manager.py::test_customer_chats_persisted_and_loaded`

### TC-014: bot 拉入已配置 squad 群 → 不覆盖

- **来源**：eval-doc #14
- **优先级**：P1
- **前置条件**：squad 群已配置
- **操作步骤**：
  1. 调用 `gm.register_customer_chat("oc_squad_1")`
  2. 调用 `gm.identify_role("oc_squad_1")`
- **预期结果**：仍返回 `"operator"`
- **涉及模块**：group_manager
- **测试文件**：`test_group_manager.py::test_bot_added_to_squad_group_skipped`

### TC-015: 成员加入 admin 群 → 获得权限

- **来源**：eval-doc #15
- **优先级**：P0
- **前置条件**：admin_chat_id 配置
- **操作步骤**：
  1. 调用 `gm.on_member_added("ou_user1", "oc_admin")`
  2. 调用 `gm.has_admin_permission("ou_user1")`
- **预期结果**：返回 True
- **涉及模块**：group_manager
- **测试文件**：`test_group_manager.py::test_member_added_to_admin_group`

### TC-016: 成员退出 squad 群 → 失去权限

- **来源**：eval-doc #16
- **优先级**：P0
- **前置条件**：成员已加入
- **操作步骤**：
  1. add → remove → `has_operator_permission()`
- **预期结果**：返回 False
- **涉及模块**：group_manager
- **测试文件**：`test_group_manager.py::test_member_removed_from_squad`

### TC-017: 群解散 → 移除 customer

- **来源**：eval-doc #17
- **优先级**：P1
- **前置条件**：customer 已注册
- **操作步骤**：
  1. register → `gm.on_group_disbanded("oc_cust1")`
  2. 调用 `gm.identify_role("oc_cust1")`
- **预期结果**：返回 `"unknown"`
- **涉及模块**：group_manager
- **测试文件**：`test_group_manager.py::test_group_disbanded_removes_customer`

### TC-018: public visibility → customer + squad 群

- **来源**：eval-doc #18
- **优先级**：P0
- **前置条件**：sender + group_manager mock
- **操作步骤**：
  1. 调用 `router.route("conv_1", {"text": "hello", "visibility": "public"})`
- **预期结果**：sender.send_text 调用 2 次（customer + squad）
- **涉及模块**：visibility_router
- **测试文件**：`test_visibility.py::test_public_goes_to_customer_and_squad`

### TC-019: side visibility → 只发 squad 群

- **来源**：eval-doc #19
- **优先级**：P0
- **前置条件**：sender + group_manager mock
- **操作步骤**：
  1. 调用 `router.route("conv_1", {"text": "advice", "visibility": "side"})`
- **预期结果**：只发到 squad，不发到 customer
- **涉及模块**：visibility_router
- **测试文件**：`test_visibility.py::test_side_only_goes_to_squad`

### TC-020: send_text API 调用

- **来源**：eval-doc #20
- **优先级**：P0
- **前置条件**：FeishuSender + mock _client
- **操作步骤**：
  1. 调用 `sender.send_text_sync("oc_xxx", "hello")`
- **预期结果**：`_client.im.v1.message.create` 被调用一次
- **涉及模块**：sender
- **测试文件**：`test_sender.py::test_send_text_calls_api`

### TC-021: send_card API 调用

- **来源**：eval-doc #21
- **优先级**：P1
- **前置条件**：FeishuSender + mock _client
- **操作步骤**：
  1. 调用 `sender.send_card_sync("oc_xxx", card_json)`
- **预期结果**：create 被调用，msg_type=interactive
- **涉及模块**：sender
- **测试文件**：`test_sender.py::test_send_card_calls_api`

### TC-022: update_message 编辑

- **来源**：eval-doc #22
- **优先级**：P1
- **前置条件**：FeishuSender + mock _client
- **操作步骤**：
  1. 调用 `sender.update_message_sync("om_xxx", "updated text")`
- **预期结果**：`_client.im.v1.message.patch` 被调用一次
- **涉及模块**：sender
- **测试文件**：`test_sender.py::test_update_message_calls_patch_api`

## 统计

| 指标 | 值 |
|------|-----|
| 总用例数 | 22 |
| P0 | 14 |
| P1 | 6 |
| P2 | 2 |
| 来源：eval-doc | 22 |

## 测试文件分布

| 测试文件 | 用例数 | 覆盖模块 |
|----------|--------|---------|
| `feishu_bridge/tests/test_parsers.py` | 8 | message_parsers |
| `feishu_bridge/tests/test_group_manager.py` | 9 | group_manager |
| `feishu_bridge/tests/test_visibility.py` | 2 | visibility_router |
| `feishu_bridge/tests/test_sender.py` | 3 | sender |

## 风险标注

- **高风险**：group_manager 的 customer 持久化逻辑（JSON 文件 I/O）— 需要 tempdir 隔离
- **回归风险**：无（feishu_bridge 是新增模块，不修改现有代码）
- **覆盖未知**：bridge.py + config.py + test_client.py 无单元测试（按计划为集成层，Phase Final 覆盖）
