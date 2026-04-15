---
type: eval-doc
id: cs-eval-prerelease-infra
status: open
mode: simulate
feature: "Pre-release 测试基础设施 — FeishuTestClient 扩展 + full_stack fixture + test_feishu_e2e.py"
producer: skill-5
submitter: yaosh
created_at: "2026-04-16"
updated_at: "2026-04-16"
related:
  - cs-plan-prerelease-infra
---

# Eval: Pre-release 测试基础设施

## 背景

Phase 4.6 + db-consolidation + card-action 全部完成（48 artifacts, 247 tests PASS）。
Pre-release 测试计划在 `docs/discuss/plan/07-phase-final-testing.md` 已完整描述。
但以下测试基础设施尚未实现：

## 缺失项

### 1. FeishuTestClient 扩展（7 个新方法）

当前只有 7 个基础方法（send_message, send_card, list_messages, get_message, assert_message_appears, assert_message_absent）。

需新增:

| 方法 | 用途 | 依赖 API |
|------|------|---------|
| `assert_message_edited(chat_id, message_id, contains, timeout)` | 验证消息被编辑 | im.v1.message.get (轮询 update_time 变化) |
| `assert_card_appears(chat_id, contains, timeout)` | 验证 squad 群收到 interactive card | list_messages + msg_type="interactive" 过滤 |
| `assert_card_updated(chat_id, contains, timeout)` | 验证 card 状态更新 | get_message + content 变化检测 |
| `send_thread_reply(chat_id, text)` | 在 thread 中回复 | im.v1.message.reply (reply_in_thread=True) |
| `assert_thread_message_appears(chat_id, contains, timeout)` | 验证 thread 中出现消息 | im.v1.message.list + root_id 过滤 |
| `send_message_as_operator(chat_id, text)` | 以 operator 身份发消息 | send_message（同一 bot，通过群身份区分） |
| `click_card_action(chat_id, action_value)` | 模拟卡片按钮点击 | 需构造 card action callback payload |

### 2. full_stack fixture

`tests/pre_release/conftest.py` 需要 full_stack fixture，7 步启动链路：
1. zchat project create prerelease-test
2. ergo IRC daemon start
3. channel-server 独立进程 (uv run zchat-channel)
4. Bridge API 可达验证
5. fast-agent 创建 (zchat agent create fast-agent)
6. deep-agent 创建
7. feishu_bridge 进程启动

清理逆序 teardown。

### 3. test_feishu_e2e.py

07-phase-final-testing.md 中有完整代码（~150 行），包含 9 个测试类 17 个 test case。
需要落地为实际文件。

### 4. click_card_action 的可行性

`click_card_action` 需要模拟飞书卡片回调。两种方案：
- **方案 A**: 直接构造 card action payload 发给 CardAwareClient（绕过飞书平台）
- **方案 B**: 通过飞书 API 发 card action 事件（可能不支持）

建议方案 A — 在测试中直接调用 bridge._on_card_action(payload) 或通过 WebSocket 发送模拟 payload。
如果不可行，该测试标记 pytest.skip("card action 需手动验证")。

## 行为预期

| # | 行为 | 验证方式 |
|---|------|---------|
| BEH-1 | assert_message_edited 检测到消息 update_time 变化 | unit test: mock get_message 返回变化后的 content |
| BEH-2 | assert_card_appears 检测 msg_type=interactive 的消息 | unit test: mock list_messages 返回 card |
| BEH-3 | assert_card_updated 检测 card content 变化 | unit test: mock get_message 先旧后新 |
| BEH-4 | send_thread_reply 使用 reply_in_thread=True | unit test: 验证 API 调用参数 |
| BEH-5 | assert_thread_message_appears 按 root_id 过滤 | unit test: mock list_messages + root_id |
| BEH-6 | send_message_as_operator 能在客户群发消息 | unit test: 验证 send_message 被调用 |
| BEH-7 | full_stack fixture 启动 7 个组件并逆序清理 | E2E: 验证 IRC + Bridge API + agents 可达 |
| BEH-8 | test_feishu_e2e.py 17 个 test case 可运行 | E2E: 至少 skeleton 可 collect |

## 风险

- full_stack 依赖真实 Claude Code session — 如果 Claude API 不可用，需要降级模式
- 飞书群 chat_id 需要真实值 — feishu-e2e-config.yaml 中 xxx 需替换
- click_card_action 可能无法完全自动化 — 需手动截图补充

## 优先级

**P0（阻塞 pre-release）**: FeishuTestClient 扩展 + full_stack + test_feishu_e2e.py
**P1（可降级）**: click_card_action 自动化（不可行则 skip + 手动截图）
