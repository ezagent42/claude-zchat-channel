"""channel-server V4 进程入口。"""

from __future__ import annotations
import asyncio
import logging
import os

from .irc_connection import IRCConnection
from .plugin import PluginRegistry
from .plugin_loader import load_plugins, load_plugins_toml
from .routing import load as load_routing
from .routing_watcher import watch_routing
from .router import Router
from .ws_server import WSServer


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


log = logging.getLogger("channel_server.boot")


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

    # ── 加载 plugin (V7 config-driven) ─────────────────────────────────
    #
    # 机制（见 channel_server.plugin_loader）:
    #   1. 扫 builtin `plugins/` namespace package + 可选用户 `~/.zchat/plugins/`
    #   2. 从 routing.toml 同目录读 plugins.toml，找 `[plugins.<name>]` section
    #   3. Signature-driven DI: loader 按 __init__ kw 名注入 emit_event / emit_command
    #      / 同 registry 内已注册的 peer plugin（如 csat 要 audit）
    #   4. 未写配置的 plugin 仍会加载，走默认 data_dir `<project>/plugins/<name>/`
    #   5. plugins.toml 里 `enabled = false` 可显式禁用
    #
    # V6→V7 去掉了 CS_DATA_DIR env var 兜底；plugin 数据路径统一由 plugins.toml
    # data_dir 字段或默认推导。E2E 测试通过 fixture 注入 tmpdir + 构造 plugins.toml。

    async def emit_event(event: str, channel: str, data: dict) -> None:
        """plugin emit event 的统一出口 → router.emit_event。"""
        await router.emit_event(channel, event, data)

    async def emit_command(cmd_name: str, channel: str, args: dict) -> None:
        """plugin emit command → 作为 "/" 消息重新进入 router 分派。"""
        from zchat_protocol import ws_messages as _ws
        cmd_msg = _ws.build_message(channel, source="internal", content=f"/{cmd_name}")
        await router.forward_inbound_ws(cmd_msg)

    plugins_toml = load_plugins_toml(routing_path)
    registered = load_plugins(
        registry=registry,
        plugins_toml=plugins_toml,
        routing_path=routing_path,
        injections={
            "emit_event": emit_event,
            "emit_command": emit_command,
        },
    )
    log.info("[boot] registered %d plugins: %s", len(registered), registered)

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

    # 等待 IRC welcome（避免在 JOIN 命令在 NICK/USER 协商前发出导致 server 静默拒绝）
    import time as _time
    _deadline = _time.time() + 5.0
    while _time.time() < _deadline and not irc_conn._connection.is_connected():
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.5)  # 给 welcome 后再缓冲一帧

    # Auto-join configured channels
    for ch_id in routing.channels:
        try:
            normalized = ch_id.lstrip("#")
            irc_conn.join(f"#{normalized}")
            log.info("[boot] joined #%s", normalized)
        except Exception:
            log.exception("[boot] join #%s failed", ch_id)

    # 启动 routing.toml watcher（热更新路由表）
    watcher_task = asyncio.create_task(
        watch_routing(routing_path, router, irc_conn, interval=2.0)
    )

    try:
        # Keep alive
        await asyncio.Event().wait()
    finally:
        watcher_task.cancel()


def main() -> None:
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
