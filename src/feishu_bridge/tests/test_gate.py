"""feishu_bridge 本地 gate 单元测试。

对应 P1-S3：channel-server 不再判决 visibility，feishu_bridge 在路由到飞书之前
本地 gate：(mode, sender_role) → 是否进客户群。

不使用 ParticipantRole 枚举，直接用字符串。
"""

from __future__ import annotations

import pytest

from feishu_bridge.gate import compute_visibility, infer_sender_role


# ---------------------------------------------------------------------- #
# compute_visibility 矩阵测试
# ---------------------------------------------------------------------- #


def test_copilot_operator_becomes_side() -> None:
    """copilot 模式 + operator → side（不进客户群）。"""
    assert compute_visibility("copilot", "operator") == "side"


def test_takeover_agent_becomes_side() -> None:
    """takeover 模式 + agent → side（operator 已接管，agent 输出不给客户看）。"""
    assert compute_visibility("takeover", "agent") == "side"


def test_auto_mode_passthrough() -> None:
    """auto 模式 + operator → public（auto/fast 模式无限制）。"""
    assert compute_visibility("auto", "operator") == "public"


def test_fast_mode_passthrough() -> None:
    """fast 模式 + agent → public（正常对话模式）。"""
    assert compute_visibility("fast", "agent") == "public"


def test_copilot_agent_passthrough() -> None:
    """copilot 模式 + agent → public（agent 回复仍发给客户）。"""
    assert compute_visibility("copilot", "agent") == "public"


def test_takeover_operator_passthrough() -> None:
    """takeover 模式 + operator → public（operator 发言给客户看）。"""
    assert compute_visibility("takeover", "operator") == "public"


def test_system_default_not_promoted() -> None:
    """default=system 直接透传——不可逆降级原则，即使 gate 规则命中也不做更改。"""
    assert compute_visibility("copilot", "operator", default="system") == "system"


def test_side_default_not_changed() -> None:
    """default=side 直接透传——已经是受限可见性。"""
    assert compute_visibility("auto", "agent", default="side") == "side"


def test_unknown_mode_role_passthrough() -> None:
    """未知的 mode+role 组合 → 返回 default（"public"）。"""
    assert compute_visibility("auto", "unknown") == "public"


def test_unknown_role_in_copilot() -> None:
    """copilot 模式 + unknown role → public（不触发任何规则）。"""
    assert compute_visibility("copilot", "unknown") == "public"


def test_custom_default_respected() -> None:
    """当 default 为 public 且无规则命中时，返回 default。"""
    assert compute_visibility("fast", "operator", default="public") == "public"


# ---------------------------------------------------------------------- #
# infer_sender_role 推断测试
# ---------------------------------------------------------------------- #


def test_infer_agent_by_nick_pattern() -> None:
    """包含 '-agent' 的 sender_id → agent。"""
    assert infer_sender_role("alice-agent0") == "agent"
    assert infer_sender_role("alice-agent") == "agent"


def test_infer_operator_by_ou_prefix() -> None:
    """以 'ou_' 开头的 sender_id → operator（飞书 open_id）。"""
    assert infer_sender_role("ou_abc123") == "operator"
    assert infer_sender_role("ou_xxxxxxxxxxxxxxxxx") == "operator"


def test_infer_unknown_empty() -> None:
    """空字符串 → unknown。"""
    assert infer_sender_role("") == "unknown"


def test_infer_unknown_other() -> None:
    """不匹配任何规则的 sender_id → unknown。"""
    assert infer_sender_role("card_action") == "unknown"
    assert infer_sender_role("some-nick") == "unknown"


def test_infer_custom_pattern() -> None:
    """自定义 agent_nick_pattern。"""
    assert infer_sender_role("claude-helper", agent_nick_pattern="-helper") == "agent"
    assert infer_sender_role("claude-helper", agent_nick_pattern="-agent") == "unknown"
