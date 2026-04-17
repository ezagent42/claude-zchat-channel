"""channel-server V4 进程入口。"""

from __future__ import annotations
import asyncio
import logging
import os

from .irc_connection import IRCConnection
from .plugin import PluginRegistry
from .routing import load as load_routing
from .router import Router
from .ws_server import WSServer

# 官方插件
from plugins.mode.plugin import ModePlugin
from plugins.sla.plugin import SlaPlugin
from plugins.resolve.plugin import ResolvePlugin


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    # Config via env
    irc_server = _env("IRC_SERVER", "127.0.0.1")
    irc_port = int(_env("IRC_PORT", "6667"))
    irc_nick = _env("CS_NICK", "cs-bot")
    irc_tls = _env("IRC_TLS", "false").lower() == "true"
    irc_password = _env("IRC_PASSWORD") or None
    ws_host = _env("WS_HOST", "127.0.0.1")
    ws_port = int(_env("WS_PORT", "9999"))
    routing_path = _env("CS_ROUTING_CONFIG", "routing.toml")

    # Load routing
    routing = load_routing(routing_path)

    # Plugin registry
    registry = PluginRegistry()

    # WS server
    ws_server = WSServer(host=ws_host, port=ws_port)

    # IRC conn（回调稍后注入）
    irc_conn = IRCConnection(
        server=irc_server,
        port=irc_port,
        nickname=irc_nick,
        use_tls=irc_tls,
        password=irc_password,
    )

    # Router
    router = Router(routing=routing, registry=registry, irc_conn=irc_conn, ws_server=ws_server)

    # ── 注册官方插件（V4-S2b）──────────────────────────────────────────

    async def emit_event(event: str, channel: str, data: dict) -> None:
        """plugin emit event 的统一出口 → router.emit_event。"""
        await router.emit_event(channel, event, data)

    async def emit_command(cmd_name: str, channel: str, args: dict) -> None:
        """plugin emit command → 作为 "/" 消息重新进入 router 分派。"""
        from zchat_protocol import ws_messages as _ws
        cmd_msg = _ws.build_message(channel, source="internal", content=f"/{cmd_name}")
        await router.forward_inbound_ws(cmd_msg)

    registry.register(ModePlugin(emit_event=emit_event))
    registry.register(SlaPlugin(emit_event=emit_event, emit_command=emit_command, timeout_seconds=180))
    registry.register(ResolvePlugin(emit_event=emit_event))

    # ──────────────────────────────────────────────────────────────────

    # Wire callbacks
    ws_server._on_inbound = router.forward_inbound_ws

    loop = asyncio.get_event_loop()

    def _on_pubmsg(channel: str, nick: str, body: str) -> None:
        asyncio.run_coroutine_threadsafe(
            router.forward_inbound_irc(channel, nick, body),
            loop,
        )

    irc_conn.on_pubmsg = _on_pubmsg

    # Start
    await ws_server.start()
    irc_conn.connect()

    # Auto-join configured channels
    for ch_id in routing.channels:
        try:
            irc_conn.join(f"#{ch_id}")
        except Exception:
            pass

    # Keep alive
    await asyncio.Event().wait()


def main() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
