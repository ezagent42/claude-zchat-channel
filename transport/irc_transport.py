"""IRC transport layer — 连接管理 + 事件 handler 提取 (spec §6)。

从 server.py 原 setup_irc + on_pubmsg + on_privmsg + _handle_sys_message 提取而来。
保持与原实现相同的 IRC 交互语义；业务逻辑回调通过构造参数注入，避免模块耦合。
"""

from __future__ import annotations

import asyncio
import functools
import os
import sys
import threading
import time
from typing import Any, Callable

import irc.client
import irc.connection

from zchat_protocol.sys_messages import (
    decode_sys_from_irc,
    encode_sys_for_irc,
    make_sys_message,
)

# IRC 命名约定：每个 conversation 对应独立的频道 #conv-<conversation_id>
CONV_CHANNEL_PREFIX = "#conv-"


def parse_agent_message(text: str) -> dict:
    """解析 agent IRC 消息前缀，返回结构化消息字典。

    前缀格式:
        __msg:<uuid>:<text>    — 普通回复，含 message_id
        __edit:<uuid>:<text>   — 编辑替换已有消息
        __side:<text>          — side channel 消息 (visibility=side)
        (无前缀)               — 普通消息，Gate 判定 visibility
    """
    if text.startswith("__edit:"):
        rest = text[len("__edit:"):]
        colon_idx = rest.find(":")
        if colon_idx == -1:
            return {"type": "reply", "text": text}
        msg_id = rest[:colon_idx]
        body = rest[colon_idx + 1:]
        return {"type": "edit", "message_id": msg_id, "text": body}

    if text.startswith("__side:"):
        body = text[len("__side:"):]
        return {"type": "side", "text": body}

    if text.startswith("__msg:"):
        rest = text[len("__msg:"):]
        colon_idx = rest.find(":")
        if colon_idx == -1:
            return {"type": "reply", "text": text}
        msg_id = rest[:colon_idx]
        body = rest[colon_idx + 1:]
        return {"type": "reply", "message_id": msg_id, "text": body}

    # 无前缀 — 普通消息
    return {"type": "reply", "text": text}


class IRCTransport:
    """封装 IRC 连接、事件分发与对话频道命名。

    - 构造后调用 `start(queue, loop, on_pubmsg=..., on_privmsg=...)` 启动。
    - 业务层通过注入 `on_pubmsg` / `on_privmsg` 回调接收 IRC 事件；handler 里可
      `loop.call_soon_threadsafe(...)` 把事件投回 asyncio。
    - 系统消息（`__zchat_sys:` 前缀）由本模块内置处理。
    """

    def __init__(
        self,
        server: str,
        port: int,
        nick: str,
        *,
        channels: list[str] | None = None,
        tls: bool = False,
        auth_token: str = "",
    ) -> None:
        self.server = server
        self.port = port
        self.nick = nick
        self.channels = channels or []
        self.tls = tls
        self.auth_token = auth_token
        self.joined_channels: set[str] = set()
        self.msg_counter = {"sent": 0, "received": 0}
        self._reactor: irc.client.Reactor | None = None
        self._connection: Any = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # 对话频道命名
    # ------------------------------------------------------------------ #

    @staticmethod
    def conv_channel_name(conversation_id: str) -> str:
        return f"{CONV_CHANNEL_PREFIX}{conversation_id}"

    @staticmethod
    def extract_conv_id(channel: str) -> str | None:
        if not channel.startswith(CONV_CHANNEL_PREFIX):
            return None
        remainder = channel[len(CONV_CHANNEL_PREFIX):]
        return remainder or None

    # ------------------------------------------------------------------ #
    # 系统消息处理（与旧 server._handle_sys_message 对齐）
    # ------------------------------------------------------------------ #

    def handle_sys_message(
        self,
        msg: dict,
        sender_nick: str,
        connection: Any,
    ) -> None:
        """处理入站 __zchat_sys: 消息，按类型回应对端。"""
        msg_type = msg.get("type", "")
        if msg_type == "sys.stop_request":
            reply = make_sys_message(
                self.nick, "sys.stop_confirmed", {}, ref_id=msg["id"]
            )
            connection.privmsg(sender_nick, encode_sys_for_irc(reply))
        elif msg_type == "sys.join_request":
            channel = msg.get("body", {}).get("channel", "").lstrip("#")
            if channel:
                connection.join(f"#{channel}")
                self.joined_channels.add(channel)
                reply = make_sys_message(
                    self.nick,
                    "sys.join_confirmed",
                    {"channel": f"#{channel}"},
                    ref_id=msg["id"],
                )
                connection.privmsg(sender_nick, encode_sys_for_irc(reply))
        elif msg_type == "sys.status_request":
            reply = make_sys_message(
                self.nick,
                "sys.status_response",
                {
                    "channels": list(self.joined_channels),
                    "messages_sent": self.msg_counter["sent"],
                    "messages_received": self.msg_counter["received"],
                },
                ref_id=msg["id"],
            )
            connection.privmsg(sender_nick, encode_sys_for_irc(reply))

    # ------------------------------------------------------------------ #
    # 启动 / 停止
    # ------------------------------------------------------------------ #

    def start(
        self,
        queue: "asyncio.Queue",
        loop: asyncio.AbstractEventLoop,
        *,
        on_pubmsg: Callable[[Any, Any], None] | None = None,
        on_privmsg_text: Callable[[str, str], None] | None = None,
    ) -> Any:
        """启动 IRC reactor（独立线程），返回 connection 对象。"""
        reactor = irc.client.Reactor()
        self._reactor = reactor

        connect_kwargs: dict = {}
        if self.tls:
            import ssl

            ctx = ssl.create_default_context()
            wrapper = functools.partial(ctx.wrap_socket, server_hostname=self.server)
            connect_kwargs["connect_factory"] = irc.connection.Factory(wrapper=wrapper)
        if self.auth_token:
            connect_kwargs["sasl_login"] = self.nick
            connect_kwargs["password"] = self.auth_token

        connection = reactor.server().connect(
            self.server, self.port, self.nick, **connect_kwargs
        )
        self._connection = connection

        def on_welcome(conn, event):
            if conn.real_nickname != self.nick:
                print(
                    f"[irc_transport] WARNING: nick mismatch! "
                    f"expected={self.nick} actual={conn.real_nickname}",
                    file=sys.stderr,
                )
            for ch in self.channels:
                ch_clean = ch.strip().lstrip("#")
                if ch_clean:
                    conn.join(f"#{ch_clean}")
                    self.joined_channels.add(ch_clean)
                    print(f"[irc_transport] Joined #{ch_clean}", file=sys.stderr)
            print(
                f"[irc_transport] {self.nick} ready on IRC "
                f"({self.server}:{self.port})",
                file=sys.stderr,
            )

        def _on_pubmsg(conn, event):
            # 过滤自己发的消息
            if event.source.nick == self.nick:
                return
            self.msg_counter["received"] += 1
            if on_pubmsg is not None:
                try:
                    on_pubmsg(conn, event)
                except Exception as e:
                    print(f"[irc_transport] pubmsg handler error: {e}", file=sys.stderr)

        def _on_privmsg(conn, event):
            nick = event.source.nick
            if nick == self.nick:
                return
            body = event.arguments[0]
            sys_msg = decode_sys_from_irc(body)
            if sys_msg is not None:
                self.handle_sys_message(sys_msg, nick, conn)
                return
            self.msg_counter["received"] += 1
            if on_privmsg_text is not None:
                try:
                    on_privmsg_text(nick, body)
                except Exception as e:
                    print(
                        f"[irc_transport] privmsg handler error: {e}",
                        file=sys.stderr,
                    )

        def on_disconnect(conn, event):
            print(
                "[irc_transport] Disconnected from IRC, reconnecting in 5s...",
                file=sys.stderr,
            )
            time.sleep(5)
            try:
                conn.reconnect()
            except Exception as e:
                print(f"[irc_transport] Reconnect failed: {e}", file=sys.stderr)

        connection.add_global_handler("welcome", on_welcome)
        connection.add_global_handler("pubmsg", _on_pubmsg)
        connection.add_global_handler("privmsg", _on_privmsg)
        connection.add_global_handler("disconnect", on_disconnect)

        def irc_thread():
            try:
                reactor.process_forever()
            except Exception as e:
                print(f"[irc_transport] IRC reactor error: {e}", file=sys.stderr)

        self._thread = threading.Thread(target=irc_thread, daemon=True)
        self._thread.start()

        return connection

    def privmsg(self, target: str, text: str) -> None:
        """Send PRIVMSG on the active connection (channel or nick)."""
        if self._connection is None:
            raise RuntimeError("IRC connection not started")
        self._connection.privmsg(target, text)
        self.msg_counter["sent"] += 1

    def join(self, channel: str) -> None:
        if self._connection is None:
            raise RuntimeError("IRC connection not started")
        ch = channel.lstrip("#")
        self._connection.join(f"#{ch}")
        self.joined_channels.add(ch)

    def disconnect(self, reason: str = "shutdown") -> None:
        if self._connection is not None:
            try:
                self._connection.disconnect(reason)
            except Exception:
                pass
