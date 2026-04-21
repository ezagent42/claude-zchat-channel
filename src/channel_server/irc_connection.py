"""IRC 长连接 + 消息收发。

编解码全部走 zchat_protocol.irc_encoding，本模块不写前缀字面量。
"""

from __future__ import annotations
import logging
import sys
import threading
from typing import Any, Callable

import irc.client
import irc.connection

from zchat_protocol import irc_encoding

log = logging.getLogger(__name__)


class IRCConnection:
    """IRC 客户端封装 — 连接管理 + 消息发送 + 事件回调注入。"""

    def __init__(
        self,
        server: str,
        port: int,
        nickname: str,
        *,
        use_tls: bool = False,
        password: str | None = None,
        on_pubmsg: Callable[[str, str, str], None] | None = None,  # (channel, nick, body)
        on_privmsg: Callable[[str, str], None] | None = None,  # (nick, body)
    ) -> None:
        self.server = server
        self.port = port
        self.nickname = nickname
        self.use_tls = use_tls
        self.password = password
        self.on_pubmsg = on_pubmsg
        self.on_privmsg = on_privmsg

        self._reactor = irc.client.Reactor()
        self._connection: Any = None
        self._thread: threading.Thread | None = None
        self._joined_channels: set[str] = set()
        # channel name (含 '#') → set[nick]，由 NAMES/JOIN/PART/QUIT/NICK 事件维护
        self._members: dict[str, set[str]] = {}

    def connect(self) -> None:
        """建立 IRC 连接并启动 reactor 线程。"""
        if self.use_tls:
            import ssl as ssl_module
            factory = irc.connection.Factory(wrapper=ssl_module.wrap_socket)
        else:
            factory = irc.connection.Factory()

        self._connection = self._reactor.server().connect(
            self.server,
            self.port,
            self.nickname,
            password=self.password or None,
            connect_factory=factory,
        )

        def _on_welcome(conn, event):
            log.info("[irc] connected as %s", self.nickname)

        def _on_pubmsg(conn, event):
            if self.on_pubmsg:
                channel = event.target
                nick = event.source.nick if event.source else "?"
                body = " ".join(event.arguments)
                try:
                    self.on_pubmsg(channel, nick, body)
                except Exception as e:
                    print(f"[irc_connection] on_pubmsg error: {e}", file=sys.stderr)

        def _on_privmsg(conn, event):
            if self.on_privmsg:
                nick = event.source.nick if event.source else "?"
                body = " ".join(event.arguments)
                try:
                    self.on_privmsg(nick, body)
                except Exception as e:
                    print(f"[irc_connection] on_privmsg error: {e}", file=sys.stderr)

        # ---- NAMES/JOIN/PART/QUIT/NICK 维护 _members 供 router NAMES 熔断 ----
        def _on_namreply(_conn, event):
            args = event.arguments
            if len(args) < 3:
                return
            ch = args[1]
            for raw in args[2].split():
                clean = raw.lstrip("@+%&~")
                if clean:
                    self._members.setdefault(ch, set()).add(clean)

        def _on_join(_conn, event):
            ch = event.target
            joiner = event.source.nick if event.source else None
            if ch and joiner:
                self._members.setdefault(ch, set()).add(joiner)

        def _on_part(_conn, event):
            ch = event.target
            leaver = event.source.nick if event.source else None
            if ch and leaver and ch in self._members:
                self._members[ch].discard(leaver)

        def _on_quit(_conn, event):
            leaver = event.source.nick if event.source else None
            if not leaver:
                return
            for nicks in self._members.values():
                nicks.discard(leaver)

        def _on_nick(_conn, event):
            old = event.source.nick if event.source else None
            new = event.target if event.target else None
            if not (old and new):
                return
            for nicks in self._members.values():
                if old in nicks:
                    nicks.discard(old)
                    nicks.add(new)

        self._connection.add_global_handler("welcome", _on_welcome)
        self._connection.add_global_handler("pubmsg", _on_pubmsg)
        self._connection.add_global_handler("privmsg", _on_privmsg)
        self._connection.add_global_handler("namreply", _on_namreply)
        self._connection.add_global_handler("join", _on_join)
        self._connection.add_global_handler("part", _on_part)
        self._connection.add_global_handler("quit", _on_quit)
        self._connection.add_global_handler("nick", _on_nick)

        self._thread = threading.Thread(target=self._reactor.process_forever, daemon=True)
        self._thread.start()

    def join(self, channel: str) -> None:
        if self._connection is None:
            raise RuntimeError("not connected")
        if not channel.startswith("#"):
            channel = f"#{channel}"
        self._connection.join(channel)
        self._joined_channels.add(channel)

    def part(self, channel: str) -> None:
        """LEAVE an IRC channel."""
        if self._connection is None:
            raise RuntimeError("not connected")
        if not channel.startswith("#"):
            channel = f"#{channel}"
        try:
            self._connection.part(channel)
        except Exception as e:
            log.warning("[irc] part %s failed: %s", channel, e)
        self._joined_channels.discard(channel)

    @property
    def joined_channels(self) -> set[str]:
        """当前已 JOIN 的 channel 集合（含 '#' 前缀）。"""
        return set(self._joined_channels)

    def names(self, channel: str) -> set[str]:
        """返回 channel 当前已知成员 nick 集合（含 '#' 前缀；空集合 = 未 JOIN 或缓存未填）。"""
        ch = channel if channel.startswith("#") else f"#{channel}"
        return set(self._members.get(ch, set()))

    def privmsg(self, target: str, content: str) -> None:
        """发送 PRIVMSG。content 已是 IRC 编码（含前缀）或 @-addressed 文本。"""
        if self._connection is None:
            raise RuntimeError("not connected")
        self._connection.privmsg(target, content)

    def send_sys(self, target: str, nick: str, sys_type: str, body: dict) -> None:
        """发送系统控制消息（__zchat_sys: 前缀）。"""
        payload = irc_encoding.make_sys_payload(nick, sys_type, body)
        encoded = irc_encoding.encode_sys(payload)
        self.privmsg(target, encoded)

    def disconnect(self) -> None:
        if self._connection is not None:
            try:
                self._connection.disconnect()
            except Exception:
                pass
