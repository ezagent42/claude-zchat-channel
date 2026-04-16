---
type: code-diff
id: cs-diff-cli
status: implemented
producer: skill-6
created_at: "2026-04-14T00:00:00Z"
related:
  - eval-doc: cs-eval-cli
  - test-plan: cs-plan-cli
---

# Code Diff: zchat CLI channel-server v1.0 配置扩展

## 修改文件

### zchat/cli/project.py

1. **新增 `_channel_server_defaults()`** — 返回 `[channel_server]` 段默认 dict：
   - `bridge_port`: 9999
   - `plugins_dir`: "plugins"
   - `db_path`: "conversations.db"
   - `timers`: takeover_wait=180, idle_timeout=300, close_timeout=3600
   - `participants`: operators=[], bridge_prefixes=["feishu-bridge", "web-bridge"], max_operator_concurrent=5

2. **新增 `generate_default_config()`** — 返回完整 config TOML 文本（无文件 I/O），
   包含 channel_server 段。将配置生成与文件写入解耦。

3. **重构 `create_project_config()`** — 调用 `generate_default_config()` 获取文本再写入。

4. **更新 `load_project_config()`** — 对已有配置文件补充 `channel_server` 默认值。

### 新增文件

- `zchat/cli/templates/fast-response/template.toml` — 快速响应模板元数据
- `zchat/cli/templates/fast-response/soul.md` — 快速响应 agent 人格
- `zchat/cli/templates/deep-thinking/template.toml` — 深度思考模板元数据
- `zchat/cli/templates/deep-thinking/soul.md` — 深度思考 agent 人格

### 新增测试

- `tests/unit/test_config_channel_server.py` — 5 个用例覆盖 Task 5.1
