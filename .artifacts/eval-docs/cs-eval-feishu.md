---
type: eval-doc
id: cs-eval-feishu
status: confirmed
producer: skill-5
created_at: "2026-04-15"
mode: simulate
feature: "飞书 Bridge — 消息解析 + 群角色映射 + visibility 路由 + card 消息"
submitter: "yaosh"
related:
  - cs-plan-feishu
  - cs-diff-feishu
  - cs-report-feishu
---

# Eval: 飞书 Bridge — 消息解析 + 群角色映射 + visibility 路由 + card 消息

## 基本信息
- 模式：模拟
- 提交人：yaosh
- 日期：2026-04-15
- 状态：confirmed
- Spec 参考：`docs/discuss/spec/channel-server/09-feishu-bridge.md`
- 参考实现：`/tmp/cc-openclaw/feishu/message_parsers.py`

## 架构适配分析

飞书 Bridge 作为独立进程，通过 WebSocket 连接 channel-server Bridge API（:9999）。核心集成点：

1. **注册**：`{"type": "register", "bridge_type": "feishu", "capabilities": ["customer", "operator", "admin"]}`
2. **消息转发**：飞书 WSS 事件 → `message_parsers.parse_message()` → Bridge API `customer_message` / `operator_message`
3. **回复路由**：Bridge API reply → `VisibilityRouter.route()` → `FeishuSender.send_text/card()`
4. **群管理**：飞书群事件 → `GroupManager` → role 映射 + 持久化

**可行性判断**：当前 bridge_api/ 已提供完整的 WebSocket 协议 + visibility 路由，engine/ 无需修改。feishu_bridge/ 完全自包含。

## Testcase 表格

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 模拟效果 | 差异描述 | 优先级 |
|---|------|---------|---------|---------|---------|---------|--------|
| 1 | text 消息解析 | parse_message 可用 | `parse_message("text", {"text": "hello"}, None, None)` | 返回 `("hello", "")` | 直接取 `content.text`，返回 `("hello", "")`。与 cc-openclaw 一致 | 无 | P0 |
| 2 | post 消息解析（富文本） | parse_message 可用 | `parse_message("post", {"title": "标题", "content": [[{"text": "段落1"}], [{"text": "段落2"}]]}, None, None)` | 返回含 "标题" + "段落1" + "段落2" 的文本 | 遍历 title + content 二维数组拼接。与 cc-openclaw `_parse_post` 逻辑一致 | 无 | P0 |
| 3 | image 消息（无 bridge） | bridge=None | `parse_message("image", {"image_key": "img_xxx"}, None, None)` | 返回描述性文本（含 "image"/"File"/"下载"） | 无 bridge 时 fallback 为描述性标签 `[image — 下载失败]` 或 `[File: image_key=img_xxx]` | 无 | P0 |
| 4 | interactive card 解析 | card 含 header + elements | 传入带 header.title + div elements 的 content | 提取 "评分" + "请评分" 等文本 | 递归提取 header.title.content + elements 中 div/markdown/action 文本。与 cc-openclaw `_parse_interactive` 一致 | 无 | P0 |
| 5 | sticker 表情包 | — | `parse_message("sticker", {}, None, None)` | 返回含 "表情" 的文本 | 返回 `("[表情包]", "")`。简单标签 | 无 | P1 |
| 6 | unknown 类型 fallback | 未注册的消息类型 | `parse_message("some_future_type", {}, None, None)` | 返回含类型名的描述 | 返回 `("[some_future_type 消息]", "")`。注册表 fallback 路径 | 无 | P0 |
| 7 | location 位置消息 | — | 传入 name + latitude + longitude | 返回含 "星巴克" 的文本 | 返回 `"[位置: 星巴克 (31.2, 121.4)]"` | 无 | P1 |
| 8 | system 消息 | template="add_member" | `parse_message("system", {"template": "add_member"}, None, None)` | 返回含 "加入"/"系统" 的文本 | 匹配 "add_member" → `"[系统: 新成员加入]"` | 无 | P2 |
| 9 | admin 群角色识别 | GroupManager(admin_chat_id="oc_admin") | `gm.identify_role("oc_admin")` | 返回 `"admin"` | 直接比较 chat_id == admin_chat_id | 无 | P0 |
| 10 | squad 群角色识别 | squad_chats 配置 | `gm.identify_role("oc_squad_1")` | 返回 `"operator"` + `get_operator_id()` 返回 `"xiaoli"` | 遍历 squad_chats 匹配 chat_id | 无 | P0 |
| 11 | 未知群返回 unknown | 无 customer 注册 | `gm.identify_role("oc_random")` | 返回 `"unknown"` | 不在 admin/squad/customer 中 → "unknown" | 无 | P0 |
| 12 | bot 拉入新群 → customer 注册 | customer_chats_path 可写 | `gm.register_customer_chat("oc_new")` → `gm.identify_role("oc_new")` | 返回 `"customer"` + 持久化到 JSON | 写入 _dynamic_customer_chats + _save_customer_chats() | 无 | P0 |
| 13 | customer 群持久化 + 重载 | 已注册 customer 群 | 创建 gm1 → register → 创建 gm2 同路径 | gm2.identify_role 返回 "customer" | 通过 JSON 文件持久化，新实例加载 | 无 | P0 |
| 14 | bot 拉入已配置 squad 群 → 不覆盖 | squad 群已配置 | `gm.register_customer_chat("oc_squad_1")` | identify_role 仍为 "operator" | register 前检查 admin/squad，已配置则跳过 | 无 | P1 |
| 15 | 成员加入 admin 群 → 获得权限 | admin_chat_id 配置 | `gm.on_member_added("ou_user1", "oc_admin")` | `has_admin_permission("ou_user1")` 为 True | 维护 admin_members set | 无 | P0 |
| 16 | 成员退出 squad 群 → 失去权限 | 成员已加入 | add → remove → `has_operator_permission()` | 返回 False | 从 squad_members 移除 | 无 | P0 |
| 17 | 群解散 → 移除 customer | customer 已注册 | `gm.on_group_disbanded("oc_cust1")` | identify_role 返回 "unknown" | 从 _dynamic_customer_chats 移除 + 持久化 | 无 | P1 |
| 18 | public visibility 路由 | sender + group_manager mock | route("conv_1", {"text": "hello", "visibility": "public"}) | customer 群 + squad 群都收到 | sender.send_text 调用 2 次（customer + squad） | 无 | P0 |
| 19 | side visibility 路由 | sender + group_manager mock | route("conv_1", {"text": "advice", "visibility": "side"}) | 只发到 squad 群 | sender.send_text 只对 squad 调用 | 无 | P0 |
| 20 | send_text API 调用 | FeishuSender + mock client | `sender.send_text_sync("oc_xxx", "hello")` | im.v1.message.create 被调用 | mock _client，验证 create 调用 | 无 | P0 |
| 21 | send_card API 调用 | FeishuSender + mock client | `sender.send_card_sync("oc_xxx", card_json)` | create 被调用，msg_type=interactive | mock 验证 | 无 | P1 |
| 22 | update_message 编辑 | FeishuSender + mock client | `sender.update_message_sync("om_xxx", "updated")` | im.v1.message.patch 被调用 | mock 验证 | 无 | P1 |

## 风险分析

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| lark-oapi SDK 版本差异 | EventDispatcher API 可能变化 | 锁定版本 + mock 测试 |
| 飞书 WSS 断连重连 | 消息丢失 | lark_oapi.ws.Client 内置重连 |
| customer_chats.json 并发写入 | 数据损坏 | 单进程写入 + 原子替换 |
| merge_forward 递归深度 | 栈溢出 | 跳过嵌套 merge_forward（cc-openclaw 已处理） |

## 后续行动

- [x] eval-doc 已注册到 .artifacts/eval-docs/
- [x] 用户已确认 testcase 表格 (status: draft → confirmed)
