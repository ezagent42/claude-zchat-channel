---
type: code-diff
id: cs-diff-sla-timers
status: confirmed
producer: skill-3
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-sla-timers
  - cs-plan-sla-timers
  - cs-report-sla-timers
---

# Code Diff: Task 4.6.7 — SLA Timer 自动触发

## 来源

- plan: `cs-plan-sla-timers`
- eval-doc: `cs-eval-sla-timers`
- spec: `docs/discuss/spec/channel-server/06-gap-fixes.md` 修复 1

## 变更文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `plugins/__init__.py` | A | 新建包 |
| `plugins/manager.py` | A | PluginManager：扫描 `plugins/*.py` 注册 `on_*` 函数，`fire(name, **kw)` 分发 |
| `plugins/sla_app.py` | A | App plugin：4 个 hook 函数（conversation_created/agent_public/placeholder_sent/edit_sent），每个调用 TimerManager.set_timer 或 cancel_timer |
| `server.py` | M | 导入 PluginManager；`CS_PLUGINS_DIR` 环境变量；`build_components` 注入 plugin_manager；`_on_customer_connect` 末尾 fire `on_conversation_created` |
| `tests/unit/test_sla_timers.py` | A | 7 unit 用例 + 1 集成用例 |
| `tests/e2e/test_sla_timers.py` | A | 1 E2E 用例（短 SLA duration fixture） |

## 改动类型

### PluginManager (`plugins/manager.py`)
- `__init__(plugins_dir: Path)` 扫描目录
- 跳过 `__init__`/`manager`/`_` 开头的文件
- `importlib.util.spec_from_file_location` 加载模块
- `inspect.getmembers(mod, callable)` + `name.startswith("on_")` 注册 hook
- `fire(name, **kwargs)` 按注册顺序调用；支持同步 + `isawaitable` 返回值
- 单个 hook 异常不影响其他（stdout 打印）

### SLA App plugin (`plugins/sla_app.py`)
- 模块级常量 `SLA_ONBOARD_DURATION_S = 3.0` 等（可测试 patch）
- `on_conversation_created(conv_id, components)` → `timer_manager.set_timer(conv_id, "sla_onboard", 3s, alert)`
- `on_agent_public_message(conv_id, components)` → `timer_manager.cancel_timer(conv_id, "sla_onboard")`
- `on_placeholder_sent(conv_id, components)` → cancel `sla_placeholder` + set `sla_slow_query(15s)`
- `on_edit_sent(conv_id, components)` → cancel `sla_slow_query`

### server.py 接线
- `CS_PLUGINS_DIR` 默认 `{server_root}/plugins`，支持 E2E 测试用 tmp dir 覆盖
- `build_components()` 构建 `plugin_manager = PluginManager(Path(CS_PLUGINS_DIR))` 并加入 components
- `_on_customer_connect` 末尾 `await plugin_manager.fire("on_conversation_created", conv_id=conv_id, components=components)`

## 影响模块

- 新增 `plugins/` 包
- `server.py` 的 component graph 多一个成员
- EventBus/TimerManager 未改动（已有能力足够）

## 风险评估

- **低**：PluginManager 完全新增，不影响已有代码路径
- `plugin_manager` 在 components 字典里是新 key；`wire_bridge_callbacks` 不强依赖（只在 `_on_customer_connect` 使用）
- 测试用 tmp plugins dir + env var 覆盖，避免修改源码测 SLA duration

## MVP 范围说明

- ✅ `sla_onboard` 全链路（set → breach event → bridge alert）
- ⏸ `sla_placeholder` / `sla_slow_query` 提供 plugin hook API，但实际触发点（复杂查询检测、占位消息发出）属于 App 层语义，由后续业务 plugin 显式调用
- ⏸ Agent public reply 自动 cancel onboard：目前 MVP 通过显式 `on_agent_public_message` hook 提供 API，IRC 监听自动 fire 留给 Phase Final 继续接入
