---
type: eval-doc
id: cs-eval-protocol-v2
status: confirmed
producer: skill-5
created_at: "2026-04-17"
mode: simulate
feature: "Bridge Protocol v2 — 基础设施/业务分离"
submitter: yaosh
related:
  - cs-eval-architecture-refactor
  - cs-eval-prerelease-bugs
  - cs-eval-route-table
---

# Eval: Bridge Protocol v2

## 基本信息
- 模式：模拟
- 提交人：yaosh
- 日期：2026-04-17
- 状态：confirmed

## 设计文档
`docs/discuss/design/bridge-protocol-v2.md`

## Testcase 表格

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 模拟效果 | 差异描述 | 优先级 |
|---|------|---------|---------|---------|---------|---------|--------|
| 1 | connect 创建 conversation + IRC channel | 全栈运行 | bridge 发 connect(sender_id, conv_id) | channel-server 创建 conversation + cs-bot JOIN #conv-{id} + agent dispatch + sys.join_request | 当前 customer_connect 有此逻辑但异常中断。v2 中 type=connect 触发同样流程 | 修复异常处理 + 统一消息类型 | P0 |
| 2 | message 入站统一 | conversation 存在 | bridge 发 message(sender_id, conv_id, text) | channel-server 不区分 sender 角色，直接路由到 IRC + Gate 判定 visibility | 当前分 customer_message / operator_message 两种处理。v2 统一为 message，Gate 根据 sender_id 查 participant role 判定 | 消除角色硬编码 | P0 |
| 3 | message 出站带 sender_id | agent 回复 | agent 回复通过 IRC → channel-server | channel-server 广播 message(sender_id=agent_nick, visibility=public, text) | 当前 reply 无 sender_id。v2 加上 | Bridge 可区分来源 | P0 |
| 4 | Bridge 根据 sender_id 路由 | feishu_bridge 收到出站 message | sender_id 是客户 open_id | 不回发客户群（客户已看到），只写 squad thread | 当前无此判断。v2 中 Bridge 自己维护 sender→角色 映射 | 新逻辑在 Bridge 层 | P0 |
| 5 | Bridge 根据 sender_id 路由 | feishu_bridge 收到出站 message | sender_id 是 agent nick | 发客户群 + 写 squad thread（public 双写） | 当前 reply 已有此行为但无 sender_id 判断 | 行为不变，加判断条件 | P0 |
| 6 | command 统一 | operator 或 admin 发命令 | bridge 发 command(sender_id, "/hijack", conv_id) | channel-server 根据 conv_id 查权限执行 | 当前分 operator_command / admin_command。v2 统一 | 消除角色硬编码 | P1 |
| 7 | Gate 配置驱动 | routing.toml 有 gate_rules | copilot 模式 operator 发消息 | Gate 查配置：(copilot, operator) → side | 当前 gate.py 已是 dict，改为从 routing.toml 加载 | 小改 | P1 |
| 8 | visibility 路由配置化 | routing.toml 有 visibility 配置 | public message 广播 | 查配置表决定发给哪些 capabilities | 当前硬编码在 ws_server.py。改为从配置加载 | 小改 | P1 |
| 9 | timestamp 全链路 | 任何消息 | 入站出站都带 timestamp | 可追踪消息时延 | 当前无 timestamp | 新增字段 | P2 |
| 10 | 回归 unit | v2 完成 | pytest tests/unit/ feishu_bridge/tests/ | 261+ passed | 行为不变 | 低风险 | P0 |
| 11 | 回归 E2E | v2 完成 | pytest tests/e2e/ | 24 passed | E2E 用 Bridge API 发消息，需更新消息格式 | E2E 需更新 | P0 |
| 12 | 手动飞书测试 | v2 完成 | 客户群发消息 → 回复不重复 + squad thread 有客户消息 + 卡片可点击 | 全链路通 | 依赖所有 bug 修复 | 最终验证 | P0 |
