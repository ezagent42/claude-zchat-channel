"""E2E: CSAT 评分闭环 — card action → Bridge API → set_csat。

绕过飞书 SDK（CI 无法连飞书），直接通过 Bridge API WebSocket
发送 customer_message + csat_score，验证 channel-server 端闭环。

验证策略：
- 创建对话 → 发送 csat_score → 确认 server 正常处理（不 crash）
- /status admin_command 验证 server 仍然响应

TC-7: test_csat_e2e_card_to_score
TC-8: test_csat_e2e_invalid_score_ignored
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


async def _create_conversation(ws, conv_id: str) -> None:
    """辅助：创建一个对话。"""
    await ws.send(
        json.dumps(
            {
                "type": "customer_connect",
                "conversation_id": conv_id,
                "customer": {"id": "csat_user", "name": "CSAT Tester"},
            }
        )
    )
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert ack["type"] == "customer_connected"
    assert ack["conversation_id"] == conv_id


async def _send_csat(ws, conv_id: str, score: int | str) -> None:
    """辅助：发送 CSAT 评分消息（模拟 bridge 转发 card action 结果）。"""
    await ws.send(
        json.dumps(
            {
                "type": "customer_message",
                "conversation_id": conv_id,
                "csat_score": score,
            }
        )
    )
    # 给 server 处理时间
    await asyncio.sleep(0.3)


async def _assert_server_responsive(ws) -> None:
    """辅助：发送 /status 验证 server 仍然响应。"""
    await ws.send(
        json.dumps(
            {
                "type": "admin_command",
                "conversation_id": "__admin",
                "admin_id": "csat_verifier",
                "command": "/status",
            }
        )
    )
    raw = await asyncio.wait_for(ws.recv(), timeout=5)
    msg = json.loads(raw)
    assert msg["type"] in ("reply", "message"), f"expected reply, got: {msg}"
    assert msg["visibility"] == "system"


# ------------------------------------------------------------------ #
# TC-7: card action → Bridge API → csat_score 被设置
# ------------------------------------------------------------------ #


async def test_csat_e2e_card_to_score(bridge_ws) -> None:
    """TC-7: 模拟 card action → Bridge API 收到 → csat_score 被设置。

    验证：customer_message + csat_score 被 server 正常处理，
    server 不 crash，仍然能响应后续命令。
    """
    conv_id = f"csat-e2e-1-{os.getpid()}"
    await _create_conversation(bridge_ws, conv_id)

    # 发送有效 CSAT 评分
    await _send_csat(bridge_ws, conv_id, 4)

    # server 仍然响应
    await _assert_server_responsive(bridge_ws)


# ------------------------------------------------------------------ #
# TC-8: 无效 score → conversation 不受影响
# ------------------------------------------------------------------ #


async def test_csat_e2e_invalid_score_ignored(bridge_ws) -> None:
    """TC-8: 无效 score → server 不 crash，conversation 不受影响。"""
    conv_id = f"csat-e2e-2-{os.getpid()}"
    await _create_conversation(bridge_ws, conv_id)

    # 发送无效评分（非数字字符串，int() 会失败）
    await _send_csat(bridge_ws, conv_id, "not_a_number")

    # server 仍然响应（int() 失败被 try/except 捕获）
    await _assert_server_responsive(bridge_ws)
