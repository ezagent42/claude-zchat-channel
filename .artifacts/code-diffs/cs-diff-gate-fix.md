---
type: code-diff
id: cs-diff-gate-fix
status: confirmed
producer: skill-3
created_at: "2026-04-15"
updated_at: "2026-04-15"
related:
  - cs-eval-gate-fix
  - cs-plan-gate-fix
  - cs-report-gate-fix
---

# Code Diff: send_event capability 过滤 — sla.breach 不广播到 customer bridge

## 来源

- plan: `cs-plan-gate-fix`
- eval-doc: `cs-eval-gate-fix`

## 变更文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `bridge_api/ws_server.py` | M | `send_event()` 新增 `target_capabilities` 参数，按 capability 过滤接收连接 |
| `server.py` | M | `_on_sla_breach` 调用 `send_event` 时传入 `target_capabilities={"operator", "admin"}` |

## 改动详情

### `bridge_api/ws_server.py` — send_event 签名扩展

**修改前：**
```python
async def send_event(
    self,
    event_type: str,
    data: dict,
    conversation_id: str,
) -> None:
    """广播协议级事件到已注册 Bridge 连接。"""
    payload = json.dumps({...})
    for conn in list(self._connections.values()):
        if conn.websocket is None:
            continue
        try:
            await conn.websocket.send(payload)
        except Exception:
            logger.exception(...)
```

**修改后：**
```python
async def send_event(
    self,
    event_type: str,
    data: dict,
    conversation_id: str,
    target_capabilities: set[str] | None = None,
) -> None:
    """广播协议级事件到已注册 Bridge 连接。

    target_capabilities 为 None 时广播到所有连接（用于 mode.changed 等全局状态通知）。
    传入角色集合时仅发送到拥有匹配 capability 的连接（用于 sla.breach 等运营事件）。
    """
    payload = json.dumps({...})
    for conn in list(self._connections.values()):
        if conn.websocket is None:
            continue
        if target_capabilities is not None:
            if not (set(conn.capabilities) & target_capabilities):
                continue
        try:
            await conn.websocket.send(payload)
        except Exception:
            logger.exception(...)
```

**关键改动**：
- 新增参数 `target_capabilities: set[str] | None = None`（默认 None 保持向后兼容）
- 循环内增加 capability 交集检查：`set(conn.capabilities) & target_capabilities` 为空集则 `continue`

### `server.py` — _on_sla_breach 指定 target_capabilities

**修改前：**
```python
await bridge_server.send_event(
    "sla.breach",
    {...},
    conv_id,
)
```

**修改后：**
```python
await bridge_server.send_event(
    "sla.breach",
    {...},
    conv_id,
    target_capabilities={"operator", "admin"},
)
```

**关键改动**：sla.breach 事件限定仅发送到 operator/admin 连接，customer bridge 不再收到。

## 影响模块

- `bridge_api/ws_server.py`：BridgeServer.send_event（签名扩展，向后兼容）
- `server.py`：wire_bridge_callbacks 中 _on_sla_breach handler

## 风险评估

- **极低**：`target_capabilities` 默认 None，所有未指定该参数的调用行为不变
- 仅 `_on_sla_breach` 一处调用指定了过滤；其余 `send_event` 调用（mode.changed 等）不受影响
