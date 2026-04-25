"""voice_bridge 进程入口。

用法:
    python -m voice_bridge --config <path/to/voice.json>
    python -m voice_bridge --config <path> --loopback     # L0 self-contained

config 文件 schema 见 voice.json.example。所有运行时参数都在 config 里。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from voice_bridge.bridge import VoiceBridge
from voice_bridge.config import VoiceBridgeConfig, load_config_from_json
from voice_bridge.session import VoiceSession
from voice_bridge.tokens import JWTValidator
from voice_bridge.ws_server import BrowserWSServer

log = logging.getLogger("voice_bridge")


def _parse_args(argv: list[str] | None = None) -> VoiceBridgeConfig:
    p = argparse.ArgumentParser(prog="voice_bridge")
    p.add_argument("--config", required=True,
                    help="voice_bridge config JSON 路径（jwt_secret + volcengine + bridge 配置）")
    p.add_argument("--loopback", action="store_true",
                    help="L0：跳过 CS，mic→ASR→TTS→speaker 本地回环")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    cfg = load_config_from_json(args.config)
    if args.loopback:
        cfg.loopback = True

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    )
    return cfg


async def _handle_browser_ws(bridge: VoiceBridge, ws, auth: dict) -> None:
    """WS 连接握手完成后运行。auth = {"channel": str, "customer": str}"""
    session = await bridge.register_session(
        channel=auth["channel"], customer=auth["customer"],
    )
    # 告知浏览器握手成功 + session 元信息
    await ws.send(f'{{"action":"session_ready","session_id":"{session.id}","channel":"{session.channel}"}}')
    log.info("ws session open: %s → channel=%s customer=%s",
             session.id, session.channel, session.customer)

    # 启动 loopback/bridge 主 loop
    if bridge.config.loopback:
        main_task = asyncio.create_task(bridge.run_loopback_session(session))
    else:
        main_task = asyncio.create_task(bridge.run_session(session))

    # 两条 loop：浏览器上行音频 → session.mic_queue；session.speaker_queue → 下行
    recv_task = asyncio.create_task(_pump_browser_to_mic(ws, session, bridge))
    send_task = asyncio.create_task(_pump_speaker_to_browser(ws, session))

    try:
        done, pending = await asyncio.wait(
            {main_task, recv_task, send_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        await bridge.drop_session(session.id)
        log.info("ws session closed: %s", session.id)


async def _pump_browser_to_mic(ws, session: VoiceSession, bridge: VoiceBridge) -> None:
    """从 WS 收 frame → 分派到 mic_queue（binary）或 control handler（text）。"""
    import json as _json
    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                await session.push_mic(bytes(msg))
                continue
            try:
                ctrl = _json.loads(msg)
            except Exception:
                continue
            action = ctrl.get("action")
            if action == "speech_start":
                await bridge.handle_barge_in(session)
            elif action == "speech_end":
                await bridge.handle_speech_end(session)
    except Exception as e:
        log.debug("browser→mic pump ended: %s", e)
    finally:
        session.close()


async def _pump_speaker_to_browser(ws, session: VoiceSession) -> None:
    """从 session.speaker_queue 取音频 → 推给浏览器 binary frame。"""
    try:
        while not session.closed:
            audio = await session.speaker_queue.get()
            if session.closed and audio == b"":
                break
            if audio:
                await ws.send(audio)
    except Exception as e:
        log.debug("speaker→browser pump ended: %s", e)


async def _main(cfg: VoiceBridgeConfig) -> int:
    bridge = VoiceBridge(cfg)

    # L1：在启动 web server 前先连 CS（若失败 voice_bridge 拒启动）
    if not cfg.loopback:
        try:
            await bridge.connect_cs()
        except Exception as e:
            log.error("failed to connect CS (%s): %s", cfg.cs_ws_url, e)
            log.error("hint: make sure channel_server is running, or use --loopback for L0")
            return 2

    async def on_connect(ws, auth):
        await _handle_browser_ws(bridge, ws, auth)

    jwt_validator = JWTValidator(secret=cfg.jwt_secret) if cfg.jwt_secret else None
    if not cfg.loopback and jwt_validator is None:
        log.error("voice_bridge prod requires jwt_secret in config (or --loopback for L0)")
        return 3

    server = BrowserWSServer(
        host=cfg.listen_host,
        port=cfg.listen_port,
        static_dir=cfg.static_path(),
        on_ws_connect=on_connect,
        jwt_validator=jwt_validator,
        serve_static=cfg.serve_static,
        jwt_secret=cfg.jwt_secret,
        public_ws_url_template=cfg.public_ws_url_template,
    )
    await server.start()

    if cfg.loopback:
        log.info("voice_bridge ready (loopback). WS: ws://%s:%d/ws  (no JWT, L0 only)",
                 cfg.listen_host, cfg.listen_port)
    else:
        log.info("voice_bridge ready. /issue → POST/GET; /ws → JWT-validated; "
                 "listen=%s:%d", cfg.listen_host, cfg.listen_port)

    stop_event = asyncio.Event()

    def _signal():
        log.info("stopping on signal")
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal)
            except NotImplementedError:
                pass  # Windows
    except RuntimeError:
        pass

    await stop_event.wait()
    await server.stop()
    if not cfg.loopback:
        await bridge.disconnect_cs()
    return 0


def main(argv: list[str] | None = None) -> int:
    cfg = _parse_args(argv)
    return asyncio.run(_main(cfg))


if __name__ == "__main__":
    sys.exit(main())
