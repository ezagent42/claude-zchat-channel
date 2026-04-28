"""测试 supervisor bridge 在收到未知 channel 消息时自动 reload routing.toml。

修复 bug：customer bridge 的 lazy_create 之后，squad bridge 的 supervised
快照还是启动时的旧值，新 channel 消息会被静默 skip。

修法：bridge._on_bridge_event 在 conv 既不是 own 也不是 supervised 时，
调 _maybe_reload_for_unknown_channel；如果 routing.toml mtime 已变 → reload。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _write_routing(path: Path, channels: list[tuple[str, str, str]]) -> None:
    """channels: list of (channel_id, bot, external_chat_id)."""
    lines = [
        '[bots.customer]',
        'lazy_create_enabled = true',
        'credential_file = "credentials/customer.json"',
        '',
        '[bots.squad]',
        'credential_file = "credentials/squad.json"',
        'supervises = ["customer"]',
        '',
    ]
    for ch_id, bot, chat_id in channels:
        lines.append(f'[channels."#{ch_id}"]')
        lines.append(f'bot = "{bot}"')
        lines.append(f'external_chat_id = "{chat_id}"')
        lines.append(f'entry_agent = "yaosh-{ch_id}-agent"')
        lines.append('')
    path.write_text("\n".join(lines))
    # 也写 credentials so bridge 别 IO crash
    cred_dir = path.parent / "credentials"
    cred_dir.mkdir(exist_ok=True)
    for name in ("customer", "squad"):
        cred_path = cred_dir / f"{name}.json"
        cred_path.write_text('{"app_id":"app","app_secret":"secret"}')


def _make_bridge(routing_path: Path, bot_name: str):
    """构造 FeishuBridge 实例，mock 掉 Lark / FeishuSender 的网络调用。"""
    with patch("feishu_bridge.bridge.lark.Client") as mock_lark, \
         patch("feishu_bridge.bridge.FeishuSender") as mock_sender, \
         patch("feishu_bridge.bridge.BridgeAPIClient") as mock_bridge_client:
        mock_lark.builder.return_value.app_id.return_value.app_secret.return_value.build.return_value = MagicMock()
        from feishu_bridge.bridge import FeishuBridge
        from feishu_bridge.config import build_config_from_routing
        cfg = build_config_from_routing(str(routing_path), bot_name)
        bridge = FeishuBridge(cfg, routing_path=str(routing_path))
    return bridge


def test_supervisor_skips_unknown_when_mtime_unchanged(tmp_path):
    """squad bridge 启动时见到 conv-001。新消息 conv-002 来时若 mtime 没变 → 不 reload。"""
    routing = tmp_path / "routing.toml"
    _write_routing(routing, [("conv-001", "customer", "oc_001")])

    bridge = _make_bridge(routing, "squad")
    # 启动后已加载：supervised 含 conv-001
    assert "conv-001" in {c.lstrip("#") for c in bridge._supervised_external_to_channel.values()}
    # 没改 routing.toml — 应直接返回 False
    assert bridge._maybe_reload_for_unknown_channel("conv-002") is False


def test_supervisor_reloads_when_routing_changed(tmp_path):
    """squad bridge 启动时见 conv-001；之后 customer bridge 加了 conv-002（routing.toml 被改）；
    squad 收到 conv-002 消息 → 触发 reload → conv-002 进入 supervised 集 → 返回 True。"""
    routing = tmp_path / "routing.toml"
    _write_routing(routing, [("conv-001", "customer", "oc_001")])

    bridge = _make_bridge(routing, "squad")
    # 故意把 _last_routing_reload_at 拨远，绕过 2s debounce
    bridge._last_routing_reload_at = 0.0

    # 模拟 customer bridge lazy_create：写新 routing 含 conv-002
    time.sleep(1.05)  # 确保 mtime 真的变化（fs 精度 1s 上限）
    _write_routing(routing, [
        ("conv-001", "customer", "oc_001"),
        ("conv-002", "customer", "oc_002"),
    ])

    # squad 收到 conv-002 消息 → reload → conv-002 现在 supervised
    became_known = bridge._maybe_reload_for_unknown_channel("conv-002")
    assert became_known is True
    assert "conv-002" in {c.lstrip("#") for c in bridge._supervised_external_to_channel.values()}


def test_non_supervisor_bot_does_not_reload(tmp_path):
    """customer bot 自己也是 lazy_create 路径管理新 channel；不需要走 supervised reload 路径。
    但如果 customer bot 收到他人 channel 消息（理论上不会），不应触发 reload。"""
    routing = tmp_path / "routing.toml"
    _write_routing(routing, [("conv-001", "customer", "oc_001")])

    bridge = _make_bridge(routing, "customer")
    bridge._last_routing_reload_at = 0.0

    # customer bridge supervised 是空的，且 lazy_create_enabled=True →
    # 仍允许 reload（因为可能是自己 lazy_create 之后的回调）
    # 这条测试主要验证：没有 supervisor 也没有 lazy_create 的 bot 不 reload
    bridge._supervised_external_to_channel = {}
    bridge.config.lazy_create.enabled = False
    assert bridge._maybe_reload_for_unknown_channel("conv-002") is False


def test_debounce_prevents_thrashing(tmp_path):
    """高频未知 channel 消息不应导致每次都读盘。"""
    routing = tmp_path / "routing.toml"
    _write_routing(routing, [("conv-001", "customer", "oc_001")])

    bridge = _make_bridge(routing, "squad")
    bridge._last_routing_reload_at = time.time()  # 刚刚 reload 过

    # 第二次立即进 → debounce 拒绝
    assert bridge._maybe_reload_for_unknown_channel("conv-X") is False
