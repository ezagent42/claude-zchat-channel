"""voice_portal plugin — /call 命令签发语音通话 URL。

触发：channel 内任何来源（agent reply / 督导 / 客户消息）以 "/call" 开头
        即触发本 plugin
处理：
  1. 从 env 读 VOICE_JWT_SECRET（必填，缺则降级为不签直发 portal_url）
  2. 从 plugin config 读 portal_url（必填）
  3. 推断 customer：如果 source 以 "voice-" 开头，说明已是语音用户（不应再
     发邀请，emit warning 直接返回）；否则取 source 后缀作 customer_id；
     仍空则匿名 anon-<8hex>
  4. 调 voice_bridge.tokens.issue_token 签 JWT
  5. emit_event("voice_url_issued", channel, {url, expires_at, customer})
     →  bridges 各自决定如何呈现给客户：
         · feishu_bridge 把 URL 文本发到飞书群（这条是给客户看的）
         · voice_bridge 收到忽略（客户还没在 voice 上）
         · audit plugin 自动记录

不发 message 直接 emit event，把"渲染语义"留给 bridge 决策（符合
"streaming/呈现是 bridge 职责"原则）。
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Awaitable, Callable

from channel_server.plugin import BasePlugin

log = logging.getLogger(__name__)

_DEFAULT_TTL = 180  # 3 分钟


class VoicePortalPlugin(BasePlugin):
    """处理 /call 命令，签 voice 通话 URL，广播为 voice_url_issued event。

    Config（plugins.toml [plugins.voice_portal]）:
      portal_url: str   # 必填，例如 "https://cs.h2os.cloud/call"
      ttl_seconds: int  # 可选，默认 180（夹 30~900）

    Env:
      VOICE_JWT_SECRET — JWT 签发密钥（必须，否则 plugin emit error event）
    """

    name = "voice_portal"

    def __init__(
        self,
        config: dict,
        emit_event: Callable[[str, str, dict], Awaitable[None]],
    ) -> None:
        self._portal_url: str = str(config.get("portal_url", "")).strip()
        ttl = int(config.get("ttl_seconds", _DEFAULT_TTL))
        self._ttl = max(30, min(900, ttl))
        self._secret: str = os.environ.get("VOICE_JWT_SECRET", "")
        self._emit_event = emit_event

        if not self._portal_url:
            log.warning(
                "voice_portal: portal_url not set in plugins.toml; "
                "/call will emit voice_unavailable event until configured"
            )
        if not self._secret:
            log.warning(
                "voice_portal: VOICE_JWT_SECRET env not set; "
                "/call will emit voice_unavailable event until configured"
            )

    def handles_commands(self) -> list[str]:
        return ["call"]

    async def on_command(self, cmd_name: str, msg: dict) -> None:
        channel = str(msg.get("channel", "")).lstrip("#")
        source = str(msg.get("source", ""))

        if not self._portal_url or not self._secret:
            await self._emit_event(
                "voice_unavailable", channel,
                {
                    "reason": "voice_portal not configured",
                    "missing": [
                        k for k, v in {
                            "portal_url (plugins.toml)": self._portal_url,
                            "VOICE_JWT_SECRET (env)": self._secret,
                        }.items() if not v
                    ],
                },
            )
            return

        # 已是语音客户的 source 不应该再发 URL（防 self-bounce）
        if source.startswith("voice-"):
            log.info("[voice_portal] /call from voice source %s ignored "
                     "(already on voice)", source)
            return

        customer = _infer_customer(source)

        # 延迟 import：plugin 启动时不必加载 jwt
        try:
            from voice_bridge.tokens import issue_token
        except Exception as e:
            log.exception("voice_bridge.tokens import failed: %s", e)
            await self._emit_event(
                "voice_unavailable", channel,
                {"reason": f"tokens module import failed: {e}"},
            )
            return

        try:
            token = issue_token(
                channel=channel,
                customer=customer,
                secret=self._secret,
                ttl_seconds=self._ttl,
            )
        except Exception as e:
            log.exception("token sign failed for channel=%s: %s", channel, e)
            await self._emit_event(
                "voice_unavailable", channel,
                {"reason": f"token sign failed: {e}"},
            )
            return

        sep = "&" if "?" in self._portal_url else "?"
        url = f"{self._portal_url}{sep}t={token}"
        expires_at = int(time.time()) + self._ttl
        log.info("[voice_portal] /call channel=%s customer=%s → URL signed (exp=%d)",
                 channel, customer, expires_at)
        # 把 URL 放 `message` 字段（不放 `url`）——
        # 这样 router._slim_for_irc 会自动将其截断到 200 字节再走 IRC sys PRIVMSG，
        # 避免超 512 字节的 MessageTooLong；而 bridges 经 WS broadcast 收到的
        # 是未截断的原始 event data，feishu_bridge 读 `.get("message")` 能拿到
        # 完整 URL 发给客户群。
        await self._emit_event(
            "voice_url_issued", channel,
            {
                "message": url,
                "expires_at": expires_at,
                "customer": customer,
                "ttl_seconds": self._ttl,
            },
        )


def _infer_customer(source: str) -> str:
    """source 推断 customer_id；推不出就匿名。

    实际 source 可能形如：
      - "feishu-customer-zhangsan" / "feishu:open_id_xxx"
      - "operator-lily"
      - "internal" (来自 emit_command 的合成)
    无法可靠区分类型，简单策略：用 source 本身作 customer 标识（feishu 那头的
    user_id），否则空白时分配 anon。
    """
    s = source.strip()
    if not s or s == "internal":
        return f"anon-{uuid.uuid4().hex[:8]}"
    return s
