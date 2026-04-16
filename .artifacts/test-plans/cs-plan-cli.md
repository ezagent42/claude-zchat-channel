---
type: test-plan
id: cs-plan-cli
status: executed
producer: skill-2
created_at: "2026-04-14T00:00:00Z"
updated_at: "2026-04-14T00:00:00Z"
related:
  - eval-doc: cs-eval-cli
  - code-diff: cs-diff-cli
---

# Test Plan: zchat CLI channel-server v1.0 配置扩展

## 来源
- eval-doc: `cs-eval-cli`
- 修改文件: `zchat/cli/project.py`, `zchat/cli/templates/fast-response/`, `zchat/cli/templates/deep-thinking/`

## 测试范围

### Task 5.1: config.toml [channel_server] 段

| TC-ID | 场景 | 测试类型 | 优先级 | 测试文件 | 断言 |
|-------|------|---------|-------|---------|------|
| TC-01 | generate_default_config 包含 channel_server | unit | P0 | test_config_channel_server.py | config["channel_server"]["bridge_port"] == 9999 |
| TC-02 | timers 完整且正确 | unit | P0 | test_config_channel_server.py | takeover_wait=180, idle_timeout=300, close_timeout=3600 |
| TC-03 | participants 默认值正确 | unit | P0 | test_config_channel_server.py | operators=[], bridge_prefixes 含 2 项, max_operator_concurrent=5 |
| TC-04 | paths 默认值正确 | unit | P1 | test_config_channel_server.py | plugins_dir="plugins", db_path="conversations.db" |
| TC-05 | create_project_config 磁盘写入含 channel_server | unit | P0 | test_config_channel_server.py | load_project_config 返回含 channel_server |

### Task 5.2: Agent 模板

| TC-ID | 场景 | 测试类型 | 优先级 | 测试文件 | 断言 |
|-------|------|---------|-------|---------|------|
| TC-06 | fast-response 模板可解析 | unit | P0 | test_template_loader.py | resolve_template_dir("fast-response") 不抛异常 |
| TC-07 | deep-thinking 模板可解析 | unit | P0 | test_template_loader.py | resolve_template_dir("deep-thinking") 不抛异常 |
| TC-08 | list_templates 包含新模板 | unit | P0 | test_template_loader.py | 返回列表含 fast-response 和 deep-thinking |
