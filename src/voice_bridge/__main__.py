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
import os
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
    p.add_argument("--asr", default="stub", choices=["stub", "volcengine", "whisper_cpp"])
    p.add_argument("--tts", default="stub", choices=["stub", "volcengine", "piper", "edge_tts"])
    p.add_argument("--channel", default="",
                    help="dev-mode 的 URL fallback channel（prod 走 JWT；voice_bridge 本身不绑 channel）")
    p.add_argument("--dev-mode", action="store_true", help="允许 URL 裸传 channel/customer 而无需 JWT")
    p.add_argument("--loopback", action="store_true", help="L0：跳过 CS，mic→ASR→TTS→speaker 本地回环")
    p.add_argument("--creds", default="",
                    help="voice credentials JSON 文件路径。内含 jwt_secret / volcengine.* 等")
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

    # credentials JSON（与 voice_portal plugin 共用同一文件）
    creds: dict = {}
    if args.creds:
        import json as _json
        from pathlib import Path as _Path
        creds_path = _Path(args.creds)
        if not creds_path.is_file():
            print(f"voice_bridge: --creds file not found: {creds_path}", flush=True)
            raise SystemExit(2)
        try:
            creds = _json.loads(creds_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"voice_bridge: failed to parse {creds_path}: {e}", flush=True)
            raise SystemExit(2)

    # jwt_secret 来源：优先 --creds JSON.jwt_secret
    cfg.jwt_secret = str(creds.get("jwt_secret", "")).strip() or cfg.jwt_secret

    # bridge 级可选配置段：{"bridge": {"serve_static": false}}
    bridge_cfg = creds.get("bridge") or {}
    if isinstance(bridge_cfg, dict) and "serve_static" in bridge_cfg:
        cfg.serve_static = bool(bridge_cfg["serve_static"])

    # Volcengine 凭证来源：优先 JSON.volcengine，fallback env（dev 便利）
    volc = creds.get("volcengine") or {}
    if cfg.asr_engine == "volcengine":
        cfg.asr_config = {
            "app_id": volc.get("app_id") or os.environ.get("VOLC_APP_ID", ""),
            "access_token": volc.get("access_token")
                              or os.environ.get("VOLC_ACCESS_TOKEN", ""),
            "language": volc.get("asr_language")
                          or os.environ.get("VOLC_ASR_LANGUAGE", "zh-CN"),
            "resource_id": volc.get("asr_resource_id")
                             or "volc.bigasr.sauc.duration",
        }
    if cfg.tts_engine == "volcengine":
        cfg.tts_config = {
            "app_id": volc.get("app_id") or os.environ.get("VOLC_APP_ID", ""),
            "access_token": volc.get("access_token")
                              or os.environ.get("VOLC_ACCESS_TOKEN", ""),
            "cluster": volc.get("tts_cluster", "volcano_tts"),
            "voice_type": volc.get("tts_voice", "BV700_streaming"),
            "language": volc.get("tts_language", "cn"),
            "sample_rate": int(volc.get("tts_sample_rate", 16000)),
        }

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
            # Text frame = JSON control message
            try:
                ctrl = _json.loads(msg)
            except Exception:
                continue
            action = ctrl.get("action")
            if action == "speech_start":
                # Barge-in: customer started talking; stop TTS + notify CS
                await bridge.handle_barge_in(session)
            elif action == "speech_end":
                await bridge.handle_speech_end(session)
            # other actions reserved
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
        serve_static=cfg.serve_static,
        jwt_secret=cfg.jwt_secret,
        public_ws_url_template=cfg.public_ws_url_template,
    )
    await server.start()

    if cfg.dev_mode:
        log.info("voice_bridge ready (dev). Open http://%s:%d/?channel=%s",
                 cfg.listen_host, cfg.listen_port, cfg.bind_channel or "<channel>")
    else:
        log.info("voice_bridge ready (prod). WS endpoint ws://%s:%d/ws?t=<JWT>",
                 cfg.listen_host, cfg.listen_port)

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
