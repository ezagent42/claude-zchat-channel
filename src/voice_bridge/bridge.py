"""VoiceBridge 核心 — 协调 ws_server（面向浏览器）+ CS 上游 + ASR/TTS engines。

Phase 1 (本文件 MVP)：L0 loopback — 浏览器说话 → ASR → 文本 → TTS → 浏览器播放，
不连 CS，不起 agent。

Phase 2+ 会加 CS WS 客户端循环（listen broadcast → TTS 到所有 session），
以及 mic → ASR → ws_messages.build_message 发 CS。
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from voice_bridge.asr.base import ASREngine, ASRResult
from voice_bridge.config import VoiceBridgeConfig
from voice_bridge.engines import build_asr, build_tts
from voice_bridge.session import SessionRegistry, VoiceSession
from voice_bridge.tts.base import TTSEngine

log = logging.getLogger(__name__)


class VoiceBridge:
    """顶层 bridge —— 创建 ASR/TTS engines + session registry，提供 session loop。

    Phase 1 只处理单个 session 的 mic → ASR → TTS → speaker loopback。
    """

    def __init__(self, config: VoiceBridgeConfig) -> None:
        self.config = config
        self.registry = SessionRegistry()
        # Phase 1 loopback：每个 session 单独 engine 实例（stub 成本可忽略）
        # 真实引擎 Phase 2 再改成进程级 singleton
        self._asr_engine_name = config.asr_engine
        self._tts_engine_name = config.tts_engine

    def make_asr(self) -> ASREngine:
        return build_asr(self._asr_engine_name, self.config.asr_config)

    def make_tts(self) -> TTSEngine:
        return build_tts(self._tts_engine_name, self.config.tts_config)

    async def register_session(
        self,
        channel: str,
        customer: str,
    ) -> VoiceSession:
        """Create + register a new session."""
        session = VoiceSession.new(channel=channel, customer=customer)
        self.registry.add(session)
        log.info(
            "session registered: id=%s channel=%s customer=%s (total=%d)",
            session.id, session.channel, session.customer, len(self.registry),
        )
        return session

    async def drop_session(self, session_id: str) -> None:
        self.registry.remove(session_id)
        log.info("session dropped: id=%s (total=%d)", session_id, len(self.registry))

    async def run_loopback_session(self, session: VoiceSession) -> None:
        """L0 模式：mic → ASR → TTS → speaker，不走 CS / IRC。

        在 WS server 握手后为每个 session 启动此任务。
        WS server 负责把浏览器上传的音频 put 到 session.mic_queue，
        把 session.speaker_queue 的 audio 推回浏览器。
        """
        asr = self.make_asr()
        tts = self.make_tts()
        await asr.open()
        await tts.open()
        try:
            async for asr_result in asr.stream(_drain_mic(session)):
                if not asr_result.is_final:
                    continue
                log.debug(
                    "[loopback session=%s] ASR final: %s",
                    session.id, asr_result.text,
                )
                async for chunk in tts.synthesize(asr_result.text):
                    await session.push_speaker(chunk.audio)
                    if session.closed:
                        break
                if session.closed:
                    break
        finally:
            await asr.close()
            await tts.close()


async def _drain_mic(session: VoiceSession) -> AsyncIterator[bytes]:
    """Helper: session.mic_queue → async iterator（遇 sentinel 停）。

    sentinel = b""（close() 时 put_nowait），见 session.py。
    """
    while True:
        try:
            chunk = await session.mic_queue.get()
        except asyncio.CancelledError:
            raise
        if session.closed and chunk == b"":
            # close() 放的 sentinel
            return
        if chunk:
            yield chunk
