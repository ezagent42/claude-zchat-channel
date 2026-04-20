"""E2E: bridge 懒创建链路测试（subprocess 真实调 zchat CLI 通过 mock 验证）。

场景：bot_added 事件 → bridge 按 lazy_create 配置生成 channel_id → 调用 zchat CLI → CLI 写 routing.toml。

测试策略：
- mock `asyncio.create_subprocess_exec`（记录被调用的参数）
- 触发 bridge._lazy_create_channel_and_agent（async）
- 断言 zchat channel create + agent create 被正确调用
"""

from __future__ import annotations

import asyncio
import tempfile
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def bridge_with_lazy_enabled(tmp_path):
    """构造一个 lazy_create enabled 的 bridge（跳过真 feishu 连接）。"""
    from feishu_bridge.config import BridgeConfig, FeishuConfig, LazyCreateConfig

    routing_path = tmp_path / "routing.toml"
    routing_path.write_text("", encoding="utf-8")

    config = BridgeConfig(
        bot_name="customer",
        feishu=FeishuConfig(app_id="cli_test_app", app_secret="secret"),
        channel_server_url="ws://127.0.0.1:9999",
        upload_dir=str(tmp_path / "uploads"),
        routing_path=str(routing_path),
        lazy_create=LazyCreateConfig(
            enabled=True,
            entry_agent_template="fast-agent",
            channel_prefix="conv-",
        ),
    )

    # 先 import 触发 module 加载，patch target 才能找到
    from feishu_bridge import bridge as _bridge_module
    with patch("lark_oapi.Client.builder") as builder_mock:
        builder_mock.return_value.app_id.return_value.app_secret.return_value.build.return_value = MagicMock()
        with patch.object(_bridge_module, "FeishuSender") as sender_mock:
            with patch.object(_bridge_module, "BridgeAPIClient") as api_mock:
                bridge = _bridge_module.FeishuBridge(config, routing_path=str(routing_path))
                yield bridge


@pytest.mark.asyncio
async def test_lazy_create_invokes_zchat_cli(bridge_with_lazy_enabled):
    """lazy_create_channel_and_agent 应调用 zchat channel create + agent create。"""
    bridge = bridge_with_lazy_enabled

    # mock subprocess：记录调用参数
    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        calls.append(list(args))
        proc = MagicMock()
        proc.returncode = 0

        async def _communicate():
            return (b"ok", b"")

        proc.communicate = _communicate
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await bridge._lazy_create_channel_and_agent("oc_abc12345xyz")

    # 至少两次调用：channel create + agent create
    assert len(calls) >= 2
    # 第一次：zchat channel create <ch> --external-chat oc_xxx --bot customer
    first = calls[0]
    assert first[0] == "zchat"
    assert first[1] == "channel"
    assert first[2] == "create"
    assert "--external-chat" in first
    assert "oc_abc12345xyz" in first
    assert "--bot" in first
    assert "customer" in first

    # 第二次：zchat agent create <name> --type fast-agent --channel <ch>
    second = calls[1]
    assert second[0] == "zchat"
    assert second[1] == "agent"
    assert second[2] == "create"
    assert "--type" in second
    assert "fast-agent" in second
    assert "--channel" in second


@pytest.mark.asyncio
async def test_lazy_create_channel_id_generation(bridge_with_lazy_enabled):
    """chat_id oc_abc12345xyz → channel_id 应为 conv-abc12345（截取 [3:11]）。"""
    bridge = bridge_with_lazy_enabled
    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        calls.append(list(args))
        proc = MagicMock()
        proc.returncode = 0

        async def _communicate():
            return (b"ok", b"")

        proc.communicate = _communicate
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await bridge._lazy_create_channel_and_agent("oc_abc12345xyz")

    # 第一次调用 args[3] 应该是 channel_id
    assert calls[0][3] == "conv-abc12345"


@pytest.mark.asyncio
async def test_lazy_create_skip_if_already_mapped(bridge_with_lazy_enabled):
    """如果 chat_id 已在映射中，不重复创建。"""
    bridge = bridge_with_lazy_enabled
    bridge._external_to_channel["oc_existing"] = "conv-existing"

    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        calls.append(list(args))
        proc = MagicMock()
        proc.returncode = 0

        async def _communicate():
            return (b"", b"")

        proc.communicate = _communicate
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await bridge._lazy_create_channel_and_agent("oc_existing")

    assert len(calls) == 0


@pytest.mark.asyncio
async def test_remove_channel_by_chat_invokes_cli(bridge_with_lazy_enabled):
    """chat_disbanded → 调 zchat channel remove --stop-agents。"""
    bridge = bridge_with_lazy_enabled
    bridge._external_to_channel["oc_gone"] = "conv-gone"

    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        calls.append(list(args))
        proc = MagicMock()
        proc.returncode = 0

        async def _communicate():
            return (b"", b"")

        proc.communicate = _communicate
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await bridge._remove_channel_by_chat("oc_gone")

    assert len(calls) == 1
    assert calls[0][:3] == ["zchat", "channel", "remove"]
    assert "conv-gone" in calls[0]
    assert "--stop-agents" in calls[0]
