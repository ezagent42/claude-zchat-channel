"""voice_bridge 进程入口。

用法:
    python -m voice_bridge [options]

    # L0 loopback (Phase 1)
    python -m voice_bridge --loopback --channel '#test-voice' --dev-mode

    # L1 with CS (Phase 2+)
    python -m voice_bridge --cs-url ws://127.0.0.1:9999 --dev-mode
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from voice_bridge.bridge import VoiceBridge
from voice_bridge.config import VoiceBridgeConfig
from voice_bridge.session import VoiceSession
from voice_bridge.tokens import JWTValidator
from voice_bridge.ws_server import BrowserWSServer

log = logging.getLogger("voice_bridge")


def _parse_args(argv: list[str] | None = None) -> VoiceBridgeConfig:
    p = argparse.ArgumentParser(prog="voice_bridge")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--cs-url", default="ws://127.0.0.1:9999")
    p.add_argument("--asr", default="stub", choices=["stub", "whisper_cpp", "volcengine"])
    p.add_argument("--tts", default="stub", choices=["stub", "piper", "edge_tts"])
    p.add_argument("--channel", default="", help="dev-mode: 固定绑死该 channel")
    p.add_argument("--dev-mode", action="store_true", help="允许 URL 裸传 channel/customer 而无需 JWT")
    p.add_argument("--loopback", action="store_true", help="L0：跳过 CS，mic→ASR→TTS→speaker 本地回环")
    p.add_argument("--jwt-secret", default="", help="Phase 3：JWT 验签密钥")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    cfg = VoiceBridgeConfig.from_env()
    cfg.listen_host = args.host
    cfg.listen_port = args.port
    cfg.cs_ws_url = args.cs_url
    cfg.asr_engine = args.asr
    cfg.tts_engine = args.tts
    cfg.bind_channel = args.channel
    cfg.dev_mode = args.dev_mode or cfg.dev_mode
    cfg.loopback = args.loopback or cfg.loopback
    cfg.jwt_secret = args.jwt_secret or cfg.jwt_secret

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    )
    return cfg


async def _handle_browser_ws(bridge: VoiceBridge, ws, auth: dict) -> None:
    """WS 连接握手完成后运行。

    auth = {"channel": str, "customer": str, "auth_mode": "dev"|"jwt"}
    """
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
    recv_task = asyncio.create_task(_pump_browser_to_mic(ws, session))
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


async def _pump_browser_to_mic(ws, session: VoiceSession) -> None:
    """从 WS 收 binary frame → session.mic_queue。"""
    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                await session.push_mic(bytes(msg))
            # ignore text frames (control messages handled elsewhere later)
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

    # L1：在启动 web server 前先连 CS（若失败 voice_bridge 拒启动，
    # 避免浏览器连上才发现 CS 没通）
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
    if not cfg.dev_mode and jwt_validator is None:
        log.error("production mode requires --jwt-secret (dev_mode=false)")
        return 3

    server = BrowserWSServer(
        host=cfg.listen_host,
        port=cfg.listen_port,
        static_dir=cfg.static_path(),
        on_ws_connect=on_connect,
        dev_mode=cfg.dev_mode,
        bind_channel=cfg.bind_channel,
        jwt_validator=jwt_validator,
    )
    await server.start()

    log.info("voice_bridge ready. Open http://%s:%d/ ?channel=%s",
             cfg.listen_host, cfg.listen_port, cfg.bind_channel or "<required>")

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
