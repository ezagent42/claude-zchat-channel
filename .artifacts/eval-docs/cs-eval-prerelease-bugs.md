---
type: eval-doc
id: cs-eval-prerelease-bugs
status: confirmed
producer: skill-5
created_at: "2026-04-16"
mode: verify
feature: "Pre-release 手动测试发现的 5 个 Bug"
submitter: yaosh
related:
  - cs-eval-architecture-refactor
  - cs-eval-route-table
---

# Eval: Pre-release 手动测试 Bug 修复

## 基本信息
- 模式：验证
- 提交人：yaosh
- 日期：2026-04-16
- 状态：confirmed

## Testcase 表格

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 实际效果 | 差异描述 | 优先级 |
|---|------|---------|---------|---------|---------|---------|--------|
| 1 | 消息去重 | 全栈运行 | 客户在飞书群发 "你好" | 收到 1 条回复 | 收到 2-3 条回复 | feishu_bridge 无 message_id 去重，飞书延迟重发事件被重复处理 | P0 |
| 2 | 卡片按钮 hijack | squad 群有 card | 点击 "接管" 按钮 | mode 切换为 takeover，card 更新 | 飞书返回 20067 错误，日志 `processor not found, type: card.action.trigger` | bridge.py 未注册 card.action.trigger handler；_on_card_action 只处理 CSAT 不处理 hijack/resolve | P0 |
| 3 | SLA 告警可读性 | 全栈运行 | 客户发消息 3 秒后 | admin 群收到含客户名称和群名称的告警 | 收到 `conv_id=oc_3e33...` 原始 ID，看不懂 | metadata 中缺少可读名称；SLA 3 秒太短 | P1 |
| 4 | Squad thread 客户消息 | 全栈运行 | 客户发 "你好"，agent 回复 | squad thread 显示 [客户] 你好 + [→客户] 回复 | squad thread 只显示 [→客户] 回复，看不到客户说了什么 | customer_message 到达时没写入 squad thread | P0 |
| 5 | #conv- channel 创建 | 全栈运行 | 客户首次发消息 | IRC 创建 #conv-{id} channel，cs-bot + agent JOIN | WeeChat 中没有新 channel，ergo 日志无 JOIN 记录 | customer_connect callback 可能抛异常，ws_server.py 无 try/except 导致连接断开 | P0 |

## 证据区

### 日志

Bug 1 — 飞书延迟事件:
```
22:39:36 - "你好" (message_id: om_x100b513b04b9b4acb3bb6dca3ac7db5)
22:41:05 - "你好" (message_id: om_x100b513b72af0ca0b36eae4ccf543d5, create_time: 1776350150252)
← 同一条消息延迟 5 分钟重发
```

Bug 2 — card action error:
```
[ERROR] handle message failed, message_type: event, err: processor not found, type: card.action.trigger
```

Bug 5 — ergo 日志无 JOIN:
```
14:34:21 : Client connected [cs-bot]
← 之后没有任何 JOIN #conv- 记录
```

### 复现环境
- WSL2 + ergo 2.18.0
- zchat channel-server branch: test/phase-final-prerelease
- 飞书 app: cli_a954c9f4d438dcb2

## 分流建议

**全部为疑似 bug** — 行为明确违反 PRD 预期：
- Bug 1: 消息幂等性缺失
- Bug 2: 卡片交互未实现
- Bug 3: 信息展示不完整
- Bug 4: 对话上下文不完整
- Bug 5: conversation channel 创建失败

## 修复方案（按架构层级）

### Bridge adapter 层（feishu_bridge/）
| Bug | 修复文件 | 修复内容 |
|-----|---------|---------|
| 1 | bridge.py | `_on_message` 加 `_processed_msg_ids: set` 去重 |
| 2 | bridge.py | `build_event_handler` 注册 card.action.trigger；新增 `_on_card_action_trigger` 分发 hijack/resolve/CSAT |
| 3 | bridge.py | `_forward_customer` 时查飞书用户名填入 metadata |
| 4 | bridge.py | `_forward_customer` 转发时同步写 squad thread `[客户] {text}` |

### 传输层（bridge_api/）
| Bug | 修复文件 | 修复内容 |
|-----|---------|---------|
| 5 | ws_server.py | `_handle_connection` 所有 callback 调用加 try/except |

### Plugin 层
| Bug | 修复文件 | 修复内容 |
|-----|---------|---------|
| 3 | plugins/sla_app.py | SLA_ONBOARD_DURATION_S 改为可配置（环境变量或 routing.toml） |

## 后续行动

- [x] eval-doc 已注册到 .artifacts/eval-docs/
- [x] 用户已确认 testcase 表格 (status: confirmed)
- [ ] 修复实现
- [ ] 回归测试 258 unit + 24 E2E
