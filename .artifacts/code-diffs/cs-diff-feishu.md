---
type: code-diff
id: cs-diff-feishu
status: confirmed
producer: skill-3
created_at: "2026-04-15"
related:
  - cs-plan-feishu
  - cs-eval-feishu
---

# Code Diff: 飞书 Bridge — Phase 4.5

## 新增文件

| 文件 | 行数 | 内容 |
|------|------|------|
| `feishu_bridge/__init__.py` | 2 | 包声明 |
| `feishu_bridge/message_parsers.py` | ~250 | 可插拔消息解析器（15+ 类型） |
| `feishu_bridge/sender.py` | ~90 | 飞书 API 封装（send/edit/card） |
| `feishu_bridge/group_manager.py` | ~130 | 群 ↔ 角色映射 + 持久化 |
| `feishu_bridge/visibility_router.py` | ~80 | visibility → 飞书群路由 |
| `feishu_bridge/config.py` | ~80 | YAML + env var 配置加载 |
| `feishu_bridge/bridge.py` | ~170 | 主类：WSS + 5 事件注册 |
| `feishu_bridge/test_client.py` | ~130 | E2E 测试辅助工具 |

## 新增测试

| 文件 | 测试数 | 覆盖 |
|------|--------|------|
| `feishu_bridge/tests/test_parsers.py` | 8 | 8 种消息类型解析 |
| `feishu_bridge/tests/test_group_manager.py` | 9 | 角色映射 + 动态注册 + 持久化 |
| `feishu_bridge/tests/test_sender.py` | 3 | API mock 调用验证 |
| `feishu_bridge/tests/test_visibility.py` | 2 | public/side 路由 |

## 影响范围

- 新增独立模块 `feishu_bridge/`，不修改现有代码
- 回归风险：无（完全自包含）
