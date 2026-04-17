"""feishu_bridge 本地 gate — mode + sender role → 飞书转发的有效 visibility。

channel-server 不再判决可见性；bridge 在路由到飞书之前本地决定。

设计原则：
- 非 public 的 default（system / side）直接透传——不做提升（不可逆降级原则）
- (mode, sender_role) 匹配 _GATE_RULES 时覆盖 visibility
- 无匹配规则时返回 default（通常是 "public"）
"""

from __future__ import annotations


# (mode, sender_role) → 强制 visibility（否则用 default）
# copilot 模式下，operator 消息不给客户看（只在 squad thread）
# takeover 模式下，agent 输出不给客户看（operator 已接管发言）
_GATE_RULES: dict[tuple[str, str], str] = {
    ("copilot", "operator"): "side",
    ("takeover", "agent"): "side",
}


def compute_visibility(
    mode: str,
    sender_role: str,
    default: str = "public",
) -> str:
    """根据当前 conversation mode + sender 身份，返回飞书渲染的 visibility。

    Args:
        mode: conversation 当前模式（"fast" / "copilot" / "takeover" 等）
        sender_role: 发送方角色（"operator" / "agent" / "unknown"）
        default: 入站消息的原始 visibility（通常来自 msg["visibility"]）

    Returns:
        最终应用的 visibility 字符串。

    规则：
    - 非 public 的 default（"system" / "side"）直接透传——不可逆降级原则
    - 查 _GATE_RULES[(mode, sender_role)]，匹配则覆盖
    - 无匹配规则返回 default
    """
    # 非 public 不做提升（system/side 已经是受限可见性，直接透传）
    if default in ("system", "side"):
        return default
    return _GATE_RULES.get((mode, sender_role), default)


def infer_sender_role(sender_id: str, agent_nick_pattern: str = "-agent") -> str:
    """根据 sender_id 推断发送方角色。

    推断规则（优先级从高到低）：
    1. sender_id 包含 agent_nick_pattern（默认 "-agent"）→ "agent"
    2. sender_id 以 "ou_" 开头（飞书 open_id 格式）→ "operator"
    3. 其他（空字符串 / 未知格式）→ "unknown"

    Args:
        sender_id: 发送方 ID（IRC nick 或飞书 open_id）
        agent_nick_pattern: 用于识别 agent 的子字符串（默认 "-agent"）

    Returns:
        "agent" | "operator" | "unknown"
    """
    if not sender_id:
        return "unknown"
    if agent_nick_pattern and agent_nick_pattern in sender_id:
        return "agent"
    if sender_id.startswith("ou_"):
        return "operator"
    return "unknown"
