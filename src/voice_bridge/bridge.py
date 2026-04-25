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
import random
import re
from typing import AsyncIterator

from zchat_protocol import ws_messages

from voice_bridge.asr.base import ASREngine
from voice_bridge.config import VoiceBridgeConfig
from voice_bridge.cs_client import CSClient
from voice_bridge.engines import build_asr, build_tts
from voice_bridge.session import SessionRegistry, VoiceSession
from voice_bridge.tts.base import TTSEngine

log = logging.getLogger(__name__)


# IRC-encoded content prefixes produced by agents (irc_encoding 内部约定):
#   __msg:<uuid>:<text>   — 普通回复（整条）
#   __edit:<uuid>:<text>  — Phase 4 streaming 支持：把"增量 append"识别为 delta
#                           TTS 只念新增部分；"完全替换"则退回念整条
#   __side:...            — 旁路消息（督导侧栏），不该念给客户
#   __zchat_sys:...       — 系统事件 JSON，永远不念
_MSG_PREFIX_RE = re.compile(r"^__msg:([^:]+):", re.DOTALL)
_EDIT_PREFIX_RE = re.compile(r"^__edit:([^:]+):", re.DOTALL)


def _parse_msg(content: str) -> tuple[str, str] | None:
    """返回 (msg_id, text) for __msg:<id>:<text>, 否则 None."""
    m = _MSG_PREFIX_RE.match(content)
    if not m:
        return None
    return m.group(1), content[m.end():]


def _parse_edit(content: str) -> tuple[str, str] | None:
    """返回 (target_msg_id, new_text) for __edit:<id>:<text>，否则 None."""
    m = _EDIT_PREFIX_RE.match(content)
    if not m:
        return None
    return m.group(1), content[m.end():]


def _strip_msg_prefix(content: str) -> str:
    """Back-compat thin wrapper: 把 `__msg:<id>:<text>` 剥成 `<text>`。"""
    parsed = _parse_msg(content)
    return parsed[1] if parsed else content


def _should_speak(content: str) -> bool:
    """判断一条 IRC content 顶层是否可能需要 TTS.

    __edit: 由 VoiceBridge 特殊处理（Phase 4 streaming delta），不在此过滤；
    但顶层 __side / __zchat_sys / 空白仍要过滤。
    """
    if not content or not content.strip():
        return False
    if content.startswith("__side:"):
        return False
    if content.startswith("__zchat_sys:"):
        return False
    return True


# 每个 channel 记住最近 N 条 msg_id → 已发出 text 累积。
# 目的：收到 __edit:<id>:<newer> 时，如果 newer 以 existing 为前缀，
# 只 TTS 前缀增长的 delta。
_MSG_BUFFER_PER_CHANNEL = 64


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
        # Phase 4 streaming：记住每 channel 最近 N 条 msg_id 的已播 text 累积。
        # 结构：{channel: {msg_id: already_spoken_text}}
        # 用于收到 __edit 时计算 prefix delta，只念新增部分。
        self._msg_buffer: dict[str, dict[str, str]] = {}

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

        voice_bridge 是**服务级** bridge（不像 feishu_bridge 一 bot 一进程）：
        一个进程同时服务任意多 channel 的 voice session（channel 由 JWT 动态解）。
        所以 instance_id 跟 bind_channel 解耦，默认用稳定字串 "voice"。
        多实例部署可通过环境变量 VOICE_BRIDGE_INSTANCE_ID 区分。
        """
        if self._cs_client is not None:
            return
        import os as _os
        inst = (instance_id
                or _os.environ.get("VOICE_BRIDGE_INSTANCE_ID")
                or "voice")
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

        Phase C：ASR final 送出后立即 TTS 一段 filler（"嗯，让我看一下"），
        盖住 agent 1-2s 思考 + 真答复 TTS 的空白时段，提升 phone-feel。
        """
        if self._cs_client is None:
            raise RuntimeError(
                "VoiceBridge.run_session requires connect_cs() first; "
                "for loopback use run_loopback_session instead."
            )
        asr = self.make_asr()
        await asr.open()
        source = f"{self.SOURCE_PREFIX}{session.customer}"
        log.info("[run_session session=%s] ASR stream starting on channel=%s",
                 session.id, session.channel)
        try:
            async for asr_result in asr.stream(_drain_mic(session)):
                log.debug("[run_session session=%s] ASR result is_final=%s text=%r",
                          session.id, asr_result.is_final, asr_result.text[:80])
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
                # Path C: 立即起一段 filler，让客户在 agent 思考时不感觉冷场
                if self.config.filler_enabled and not session.speaker_muted:
                    task = asyncio.create_task(self._play_filler(session))
                    self._bg_tasks.add(task)
                    task.add_done_callback(self._bg_tasks.discard)
        except Exception as e:
            log.exception("[run_session session=%s] ASR stream FAILED: %s",
                          session.id, e)
        finally:
            await asr.close()
            log.info("[run_session session=%s] ASR stream ended", session.id)

    async def _play_filler(self, session: VoiceSession) -> None:
        """Path C：随机选一句 filler TTS 推到 session.speaker_queue。

        若 session 已有未播完的 audio buffer（说明上一条 agent 答复还在排队），
        跳过 filler 防叠加。
        若 session 在过程中被 mute（barge-in），中途停止。
        """
        if session.closed or session.speaker_muted:
            return
        if not session.speaker_queue.empty():
            # 已有 audio 待播 → 不发 filler 避免堆积
            return
        phrases = self.config.filler_phrases or []
        if not phrases:
            return
        text = random.choice(phrases)
        log.debug("[filler session=%s] %s", session.id, text)
        tts = self.make_tts()
        await tts.open()
        try:
            async for chunk in tts.synthesize(text):
                if session.closed or session.speaker_muted:
                    break
                await session.push_speaker(chunk.audio)
        finally:
            await tts.close()

    async def _on_cs_message(self, msg: dict) -> None:
        """CS broadcast handler: any bridge's message / event lands here.

        类型决定行为：
          - type != "message" → 忽略（event / registered / etc.）
          - __side / __zchat_sys / 空白 → 不念
          - __msg:<id>:<text> → TTS 完整 text，记 buffer[channel][id]=text
          - __edit:<id>:<new_text>:
              - buffer 里有 id 且 new_text 以 buffer[id] 为前缀 → TTS delta
                （这是 streaming 增量 append；Phase 4 主场景）
              - buffer 有但 new_text 不是前缀（真正的替换）→ 念整条新 text
              - buffer 没有（voice_bridge 刚起 / agent edit 更老消息）→ 念整条
          - 其他裸 text（没前缀）→ TTS 整条

        过滤自己发出去再 bounce 回来的消息（source 以 SOURCE_PREFIX 开头）。
        """
        if msg.get("type") != ws_messages.WSType.MESSAGE:
            return
        channel = str(msg.get("channel", "")).lstrip("#")
        if not channel:
            return
        sessions = self.registry.sessions_for_channel(channel)
        if not sessions:
            return  # 没人在听
        source = str(msg.get("source", ""))
        if source.startswith(self.SOURCE_PREFIX):
            return  # self-echo
        content = str(msg.get("content", ""))
        if not _should_speak(content):
            return

        to_speak = self._extract_speakable(channel, content)
        if not to_speak:
            return
        log.info("[voice-out channel=%s sessions=%d] TTS: %s",
                 channel, len(sessions), to_speak[:80])
        task = asyncio.create_task(self._fanout_tts(sessions, to_speak))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _extract_speakable(self, channel: str, content: str) -> str:
        """Return the text to TTS (empty = nothing new to say).

        Updates self._msg_buffer[channel] as a side-effect.
        """
        buf = self._msg_buffer.setdefault(channel, {})

        parsed_edit = _parse_edit(content)
        if parsed_edit is not None:
            msg_id, new_text = parsed_edit
            prior = buf.get(msg_id)
            if prior is not None and new_text.startswith(prior):
                # prefix-append (streaming) → 只念 delta
                delta = new_text[len(prior):]
                if not delta.strip():
                    # 仅尾部标点/空白变化，跳过
                    return ""
                buf[msg_id] = new_text
                self._trim_buffer(channel)
                return delta
            # 替换 / 未知 id → 念整条新 text（安全回退）
            buf[msg_id] = new_text
            self._trim_buffer(channel)
            return new_text

        parsed_msg = _parse_msg(content)
        if parsed_msg is not None:
            msg_id, text = parsed_msg
            buf[msg_id] = text
            self._trim_buffer(channel)
            return text

        # 没前缀的裸 text → 念整条
        return content

    def _trim_buffer(self, channel: str) -> None:
        """Keep per-channel buffer bounded."""
        buf = self._msg_buffer.get(channel)
        if not buf:
            return
        overflow = len(buf) - _MSG_BUFFER_PER_CHANNEL
        if overflow <= 0:
            return
        # dict 插入保留顺序（Python ≥3.7），drop 最旧的若干个
        for _ in range(overflow + 8):
            try:
                first_key = next(iter(buf))
            except StopIteration:
                return
            buf.pop(first_key, None)

    async def _fanout_tts(self, sessions: list, text: str) -> None:
        """合成一次 TTS，广播到 N 个 session 的 speaker_queue。

        若某 session 在 barge-in 期间（speaker_muted=True），该 session
        的 push_speaker 是 no-op，不会 block 别的 session。
        """
        tts = self.make_tts()
        await tts.open()
        try:
            async for chunk in tts.synthesize(text):
                for session in sessions:
                    if not session.closed:
                        await session.push_speaker(chunk.audio)
        finally:
            await tts.close()

    # ------------------------------------------------------------------
    # Phase 5: barge-in
    # ------------------------------------------------------------------

    async def handle_barge_in(self, session: VoiceSession) -> None:
        """Customer started talking while agent was speaking — stop TTS + notify.

        Actions:
          1. Mute the session's speaker (new TTS chunks ignored)
          2. Drain speaker_queue (pending buffered audio dropped)
          3. Send `user_barge_in` event to CS; agent soul may listen (future).

        Design note: doesn't cancel the global fanout task because other
        sessions on the same channel (if any) should continue hearing the
        reply. Per-session mute ensures this session stops.
        """
        if session.closed:
            return
        session.speaker_muted = True
        dropped = session.drain_speaker()
        log.info("[barge-in session=%s channel=%s] dropped %d queued audio chunks",
                 session.id, session.channel, dropped)
        if self._cs_client is not None:
            await self._cs_client.send(ws_messages.build_event(
                channel=session.channel,
                event="user_barge_in",
                data={
                    "session_id": session.id,
                    "customer": session.customer,
                    "source": f"{self.SOURCE_PREFIX}{session.customer}",
                },
            ))

    async def handle_speech_end(self, session: VoiceSession) -> None:
        """Customer stopped talking — unmute speaker so new TTS plays again."""
        if session.closed:
            return
        session.speaker_muted = False
        log.info("[speech-end session=%s] speaker unmuted", session.id)


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
