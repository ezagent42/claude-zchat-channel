"""Voice portal JWT token 签发 / 验签。

Agent 通过 MCP tool `voice_issue_link` 生成 token → 喊到飞书群。
客户点链接 → voice_bridge WS server 收到 token → 调用 JWTValidator.validate
解出 channel + customer，session 随之绑定。

设计：
- HS256（对称密钥），agent_mcp 和 voice_bridge 共享 secret
- claims: channel / customer / exp / nonce (压缩字段名，URL 控制在 IRC 单条 PRIVMSG ~390 字节内)
- TTL 默认 180s（3 分钟），覆盖典型客户"看到链接 → 点击"的时间
- nonce 防止同一 token 被多次复用（voice_bridge 本地维护最近 nonce 集）
"""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field

import jwt  # pyjwt

log = logging.getLogger(__name__)

_DEFAULT_TTL = 180
_ALGO = "HS256"


class TokenError(Exception):
    """Token 校验失败（无效签名 / 过期 / 重用 / 字段缺失）。"""


@dataclass
class VoiceClaims:
    channel: str      # IRC channel 裸名（无 '#'）
    customer: str     # 客户标识
    exp: int
    nonce: str


# Compact field names — JSON 越短，base64 后 JWT 越小，URL 越短
# 飞书 IRC 单条 PRIVMSG ~390 字节 payload，URL 必须装在一条里防止
# agent_mcp.chunk_message 把 URL 切成两条客户复制时少尾巴。
# 完整中文注解见 015 runbook §"为什么 JWT 字段是单字母"。
_FIELD_CHANNEL = "c"
_FIELD_CUSTOMER = "u"
_FIELD_EXP = "exp"     # 标准 JWT 字段，保持
_FIELD_NONCE = "n"


def issue_token(
    *,
    channel: str,
    customer: str,
    secret: str,
    ttl_seconds: int = _DEFAULT_TTL,
    now: int | None = None,
) -> str:
    """Issue a voice portal JWT.

    Raises:
        ValueError if secret is empty
    """
    if not secret:
        raise ValueError("jwt secret must be non-empty")
    now = int(now if now is not None else time.time())
    payload = {
        _FIELD_CHANNEL: channel.lstrip("#"),
        _FIELD_CUSTOMER: customer,
        _FIELD_EXP: now + max(1, ttl_seconds),
        _FIELD_NONCE: secrets.token_urlsafe(6),  # 6→8 chars，仍 64bit anti-replay 强度
    }
    return jwt.encode(payload, secret, algorithm=_ALGO)


def validate_token(
    token: str,
    *,
    secret: str,
    now: int | None = None,
) -> VoiceClaims:
    """Validate + decode a token. Raises TokenError on any failure.

    Does NOT check nonce replay — that's the caller's responsibility (keeping
    set of used nonces). See JWTValidator below for the replay-safe wrapper.
    """
    if not secret:
        raise TokenError("jwt secret not configured")
    try:
        payload = jwt.decode(
            token, secret,
            algorithms=[_ALGO],
            options={"require": [_FIELD_EXP, _FIELD_CHANNEL, _FIELD_CUSTOMER, _FIELD_NONCE]},
        )
    except jwt.ExpiredSignatureError as e:
        raise TokenError("token expired") from e
    except jwt.MissingRequiredClaimError as e:
        raise TokenError(f"missing claim: {e.claim}") from e
    except jwt.InvalidTokenError as e:
        raise TokenError(f"invalid token: {e}") from e
    return VoiceClaims(
        channel=str(payload[_FIELD_CHANNEL]),
        customer=str(payload[_FIELD_CUSTOMER]),
        exp=int(payload[_FIELD_EXP]),
        nonce=str(payload[_FIELD_NONCE]),
    )


@dataclass
class JWTValidator:
    """Replay-safe token validator.

    维护最近 N 个用过的 nonce；同一 token 不能用第二次。
    """

    secret: str
    max_nonces: int = 1024
    _used: set[str] = field(default_factory=set)

    def validate(self, token: str, now: int | None = None) -> dict | None:
        """返回 claims dict（与 WS server 预期的格式对齐）；None = rejected。"""
        try:
            claims = validate_token(token, secret=self.secret, now=now)
        except TokenError as e:
            log.info("jwt rejected: %s", e)
            return None
        if claims.nonce in self._used:
            log.info("jwt rejected: nonce replay (%s)", claims.nonce)
            return None
        # Record; trim if grew too big
        self._used.add(claims.nonce)
        overflow = len(self._used) - self.max_nonces
        if overflow > 0:
            # Drop arbitrary elements to keep set bounded (simple LRU is overkill;
            # nonces rotate naturally with TTL)
            to_drop = min(overflow + 64, len(self._used) - 1)
            for _ in range(to_drop):
                if not self._used:
                    break
                self._used.pop()
        return {
            "channel": claims.channel,
            "customer": claims.customer,
            "exp": claims.exp,
            "nonce": claims.nonce,
        }
