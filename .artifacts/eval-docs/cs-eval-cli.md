---
type: eval-doc
id: cs-eval-cli
status: confirmed
producer: skill-5
created_at: "2026-04-14T00:00:00Z"
mode: simulate
feature: zchat CLI 扩展 — channel-server v1.0 配置
submitter: yaosh
related: []
---

# Eval: zchat CLI 扩展 — channel-server v1.0 配置

## 基本信息
- 模式：simulate
- 提交人：yaosh
- 日期：2026-04-14
- 状态：confirmed

## Feature 描述

zchat CLI 的 `project create` 命令生成的 `config.toml` 需要包含 `[channel_server]` 段，
为 channel-server v1.0 提供默认配置（bridge_port、timers、participants 等）。
同时新增 `fast-response` 和 `deep-thinking` 两个 agent 模板。

## Testcase 表格

| # | 场景 | 前置条件 | 操作步骤 | 预期效果 | 优先级 |
|---|------|---------|---------|---------|--------|
| 1 | generate_default_config 包含 channel_server | 无 | 调用 generate_default_config() | config 包含 channel_server.bridge_port=9999 | P0 |
| 2 | channel_server.timers 完整 | 无 | 解析 generate_default_config() 输出 | takeover_wait=180, idle_timeout=300, close_timeout=3600 | P0 |
| 3 | channel_server.participants 正确 | 无 | 解析 generate_default_config() 输出 | operators=[], bridge_prefixes 含 2 项, max_operator_concurrent=5 | P0 |
| 4 | channel_server paths 默认值 | 无 | 解析 generate_default_config() 输出 | plugins_dir="plugins", db_path="conversations.db" | P1 |
| 5 | create_project_config 写入磁盘包含 channel_server | tmp_path | create_project_config + load_project_config | channel_server 段存在且 bridge_port=9999 | P0 |
| 6 | fast-response 模板可发现 | 内置模板目录存在 | list_templates() | 返回列表含 fast-response | P0 |
| 7 | deep-thinking 模板可发现 | 内置模板目录存在 | list_templates() | 返回列表含 deep-thinking | P0 |
