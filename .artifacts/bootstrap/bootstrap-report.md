# Bootstrap Report · zchat-channel-server

> Skill 0 · 2026-04-21 V6 finalize 后重跑

## 环境

Python 3.13.5 + uv + lark_oapi + websockets。Unit baseline 183 passed / 0 failed / 0 skipped。

## 6 模块

1. **channel_server** — 核心路由（router, routing, irc_connection, plugin, ws_server, boot-main）— 含 NAMES 熔断 + emit_event slim text
2. **agent_mcp** — 独立 MCP stdio 代理（reply, run_zchat_cli, join_channel, list_peers） + IRC @mention 注入
3. **feishu_bridge** — 飞书 WSS/REST 桥接：bridge + outbound + sender + renderer + ChannelMapper + routing_reader；含 V6 supervise 链 + CSAT + chat_info 事件
4. **plugins** — 6 个 plugin：mode / sla / resolve / audit / activation / csat（plugins 不 import channel_server，依赖隔离测试保护）
5. **tests** — unit 19 文件 / e2e 4 文件
6. **meta** — pyproject / pytest.ini / plugin.json / instructions.md

## 红线审计

- `src/channel_server/` 代码级**零业务名**
- `router.py:70-71` 注释提到 customer bridge / squad bridge — 虽不 import，注释级泄露，建议 Stage 4 改成更中性的"outbound bridge / supervisor bridge"
- `src/feishu_bridge/` 允许业务语义（bridge 是业务层）
- `src/plugins/audit/`、`csat/` 内部有业务名（event payload 如 `"source": "customer"`）— 这是 event schema 的约定，不跨层

## E2E 失败（Stage 4 待修）

| 测试 | 根因 | 修法 |
|---|---|---|
| test_csat_full_lifecycle | CSAT 从 message 通道改 event，fixture 还发 `__csat_score:5` | 改 fixture 发 `build_event("csat_score", {"score":5})` |
| test_csat_multiple_channels | 同上 | 同上 |
| test_operator_responds_in_time | source 判定从 `operator_xxx` 改 `cs-bot` 判 bridge relay | fixture source 改 `cs-bot` |
| test_operator_no_response_emits_timeout | reason 从 `operator_no_response` 改 `no_human_response` | 断言字符串改 |

## 下一步

Stage 4 修这 4 个 E2E + router.py:70-71 注释整改。
