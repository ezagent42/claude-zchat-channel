"""VoiceBridge 核心 — 协调 ws_server（面向浏览器）+ CS 上游 + ASR/TTS engines。

两种运行模式（config.loopback 决定）：

L0 loopback (Phase 1)：浏览器说话 → ASR → TTS → 浏览器播放，不连 CS。
  调用方用 run_loopback_session(session)。

L1 CS-connected (Phase 2)：
  - mic → ASR → 当 final → cs_client.send(build_message(channel, source, text))
  - CS broadcast message → handle_cs_message → 匹配 channel → TTS → 广播给
    registry.sessions_for_channel 中所有 session（N:1 支持）
  调用方先 start() 建 CS 连接，然后每个 session 跑 run_session(session)。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncIterator

from zchat_protocol import ws_messages

from voice_bridge.asr.base import ASREngine, ASRResult
from voice_bridge.config import VoiceBridgeConfig
from voice_bridge.cs_client import CSClient
from voice_bridge.engines import build_asr, build_tts
from voice_bridge.session import SessionRegistry, VoiceSession
from voice_bridge.tts.base import TTSEngine

log = logging.getLogger(__name__)


# IRC-encoded content prefixes produced by agents (irc_encoding 内部约定):
#   __msg:<uuid>:<text>   — 普通回复
#   __side:...            — 旁路消息（督导侧栏），不该念给客户
#   __edit:...            — 编辑先前消息，Phase 4 可增量 TTS 中再处理
#   __zchat_sys:...       — 系统事件 JSON，永远不念
_MSG_PREFIX_RE = re.compile(r"^__msg:[^:]+:", re.DOTALL)


def _strip_msg_prefix(content: str) -> str:
    """把 `__msg:<id>:<text>` 剥成 `<text>`；其他前缀原样返回。"""
    match = _MSG_PREFIX_RE.match(content)
    if match:
        return content[match.end():]
    return content


def _should_speak(content: str) -> bool:
    """判断一条 IRC content 是否要 TTS。

    排除：
      - __side:   旁路
      - __zchat_sys:  系统 JSON
      - __edit:   Phase 4 再处理
      - 空白
    """
    if not content or not content.strip():
        return False
    if content.startswith("__side:"):
        return False
    if content.startswith("__zchat_sys:"):
        return False
    if content.startswith("__edit:"):
        return False
    return True


class VoiceBridge:
    """顶层 bridge —— engine factory + session registry + CS 连接。

    Phase 1 (loopback)：单 session 独立 ASR/TTS pipeline，不经 CS。
    Phase 2 (L1)：CS 广播驱动 TTS 到 channel 上所有 session；
                  mic → ASR → build_message 发 CS。
    """

    # 传给 CS 的 source 字段前缀：voice-<customer_id>。
    # 这个值也用于"自发回环过滤"——CS broadcast 带回自己发的消息时要忽略。
    SOURCE_PREFIX = "voice-"

    def __init__(self, config: VoiceBridgeConfig) -> None:
        self.config = config
        self.registry = SessionRegistry()
        self._asr_engine_name = config.asr_engine
        self._tts_engine_name = config.tts_engine
        self._cs_client: CSClient | None = None
        # CS 连上后暂存活跃的 broadcast handler task（取消用）
        self._bg_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # engine factories
    # ------------------------------------------------------------------

    def make_asr(self) -> ASREngine:
        return build_asr(self._asr_engine_name, self.config.asr_config)

    def make_tts(self) -> TTSEngine:
        return build_tts(self._tts_engine_name, self.config.tts_config)

    # ------------------------------------------------------------------
    # CS connection lifecycle
    # ------------------------------------------------------------------

    async def connect_cs(self, instance_id: str | None = None) -> None:
        """Establish WS connection to channel_server (L1 mode).

        Instance_id 必须在 CS 上唯一；默认用 `voice-<bind_channel>` 或生成。
        """
        if self._cs_client is not None:
            return
        inst = instance_id or (
            f"voice-{self.config.bind_channel}" if self.config.bind_channel
            else f"voice-{id(self):x}"
        )
        client = CSClient(
            url=self.config.cs_ws_url,
            instance_id=inst,
            bridge_type="voice",
            reconnect_delay=3.0,
        )
        client.on_message = self._on_cs_message
        await client.connect()
        self._cs_client = client
        log.info("voice_bridge CS connected: %s instance=%s",
                 self.config.cs_ws_url, inst)

    async def disconnect_cs(self) -> None:
        if self._cs_client is None:
            return
        await self._cs_client.close()
        self._cs_client = None
        for t in list(self._bg_tasks):
            t.cancel()

    # ------------------------------------------------------------------
    # session lifecycle
    # ------------------------------------------------------------------

    async def register_session(self, channel: str, customer: str) -> VoiceSession:
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

    # ------------------------------------------------------------------
    # L0: loopback
    # ------------------------------------------------------------------

    async def run_loopback_session(self, session: VoiceSession) -> None:
        """L0 模式：mic → ASR → TTS → speaker，不走 CS。"""
        asr = self.make_asr()
        tts = self.make_tts()
        await asr.open()
        await tts.open()
        try:
            async for asr_result in asr.stream(_drain_mic(session)):
                if not asr_result.is_final:
                    continue
                log.debug("[loopback session=%s] ASR final: %s",
                          session.id, asr_result.text)
                async for chunk in tts.synthesize(asr_result.text):
                    await session.push_speaker(chunk.audio)
                    if session.closed:
                        break
                if session.closed:
                    break
        finally:
            await asr.close()
            await tts.close()

    # ------------------------------------------------------------------
    # L1: CS-connected
    # ------------------------------------------------------------------

    async def run_session(self, session: VoiceSession) -> None:
        """L1 模式：mic → ASR → CS.send；CS broadcast 在 _on_cs_message 已处理。

        一个 session 生命周期内持有一个 ASR engine 实例。
        TTS 的触发不在本函数（由 CS broadcast 驱动，跨 session 共享），
        所以 TTS engine 不在这里创建。
        """
        if self._cs_client is None:
            raise RuntimeError(
                "VoiceBridge.run_session requires connect_cs() first; "
                "for loopback use run_loopback_session instead."
            )
        asr = self.make_asr()
        await asr.open()
        source = f"{self.SOURCE_PREFIX}{session.customer}"
        try:
            async for asr_result in asr.stream(_drain_mic(session)):
                if not asr_result.is_final:
                    # Phase 4 会 emit interim 到 CS；现在只发 final
                    continue
                text = asr_result.text.strip()
                if not text:
                    continue
                log.info("[voice-in session=%s] channel=%s text=%s",
                         session.id, session.channel, text[:80])
                msg = ws_messages.build_message(
                    channel=session.channel,
                    source=source,
                    content=text,
                )
                await self._cs_client.send(msg)
        finally:
            await asr.close()

    async def _on_cs_message(self, msg: dict) -> None:
        """CS broadcast handler: any bridge's message / event lands here.

        我们只在 type=message 且 channel 上有活跃 session 时 TTS。
        过滤自己发出去再 bounce 回来的消息（source 以 SOURCE_PREFIX 开头）。
        """
        mtype = msg.get("type")
        if mtype != ws_messages.WSType.MESSAGE:
            return
        channel = str(msg.get("channel", "")).lstrip("#")
        if not channel:
            return
        sessions = self.registry.sessions_for_channel(channel)
        if not sessions:
            return  # 没人在听
        source = str(msg.get("source", ""))
        if source.startswith(self.SOURCE_PREFIX):
            # 自己发的回环（例如 CS broadcast 给所有 bridge 包括发送方）
            return
        content = str(msg.get("content", ""))
        if not _should_speak(content):
            return
        spoken = _strip_msg_prefix(content)
        log.info("[voice-out channel=%s sessions=%d] TTS: %s",
                 channel, len(sessions), spoken[:80])
        # TTS 单次合成 → 分发给该 channel 所有 session
        task = asyncio.create_task(self._fanout_tts(sessions, spoken))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _fanout_tts(self, sessions: list, text: str) -> None:
        """合成一次 TTS，广播到 N 个 session 的 speaker_queue。"""
        tts = self.make_tts()
        await tts.open()
        try:
            async for chunk in tts.synthesize(text):
                for session in sessions:
                    if not session.closed:
                        await session.push_speaker(chunk.audio)
        finally:
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
            return
        if chunk:
            yield chunk
