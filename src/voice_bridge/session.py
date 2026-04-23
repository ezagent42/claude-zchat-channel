"""VoiceSession — 一个浏览器连接的全部运行期状态。

一个 session = 一个客户浏览器打开 /call 后建立的 WS 连接。
channel：IRC channel 裸名（不带 '#'），session 期间固定。
N:1：一个 channel 可以有多个 session 同时挂着，每人独立 ASR pipeline。

Session 不直接处理 WS 消息（那是 ws_server 的事），而是封装音频进出 queue
+ 身份元信息，供 bridge 核心循环使用。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class VoiceSession:
    """一次浏览器通话的状态。

    Attributes:
        id: 唯一标识（uuid4 hex）
        channel: IRC channel 裸名，如 "conv-001"（无 '#'）
        customer: 客户标识，如 "zhangsan" / "anon-a3b4c5"
        started_at: unix 秒
        mic_queue: 浏览器上传的音频 PCM chunks 入口
        speaker_queue: TTS 合成的音频 PCM chunks 出口（推给浏览器）
        closed: 是否已关闭（WS 断 / 超时）
    """
    id: str
    channel: str
    customer: str
    started_at: float = field(default_factory=time.time)
    mic_queue: asyncio.Queue[bytes] = field(default_factory=asyncio.Queue)
    speaker_queue: asyncio.Queue[bytes] = field(default_factory=asyncio.Queue)
    closed: bool = False

    @classmethod
    def new(cls, channel: str, customer: str) -> "VoiceSession":
        """Factory：normalize channel 去掉 '#' 前缀。"""
        return cls(
            id=uuid.uuid4().hex,
            channel=channel.lstrip("#"),
            customer=customer,
        )

    async def push_mic(self, audio: bytes) -> None:
        """浏览器上传的音频 → ASR pipeline。"""
        if self.closed:
            return
        await self.mic_queue.put(audio)

    async def push_speaker(self, audio: bytes) -> None:
        """TTS 合成的音频 → 推浏览器。"""
        if self.closed:
            return
        await self.speaker_queue.put(audio)

    def close(self) -> None:
        """Mark closed + unblock any queue consumer waiting."""
        self.closed = True
        # Unblock consumers waiting on queue.get()
        # by putting sentinel b"" that loops check for.
        try:
            self.mic_queue.put_nowait(b"")
        except asyncio.QueueFull:
            pass
        try:
            self.speaker_queue.put_nowait(b"")
        except asyncio.QueueFull:
            pass


class SessionRegistry:
    """活跃 session 的 in-memory 注册表。

    按 channel 索引，支持 N:1 (channel 下多个 session)。
    """

    def __init__(self) -> None:
        self._by_channel: dict[str, list[VoiceSession]] = {}
        self._by_id: dict[str, VoiceSession] = {}

    def add(self, session: VoiceSession) -> None:
        self._by_id[session.id] = session
        self._by_channel.setdefault(session.channel, []).append(session)

    def remove(self, session_id: str) -> VoiceSession | None:
        session = self._by_id.pop(session_id, None)
        if session is None:
            return None
        channel_list = self._by_channel.get(session.channel)
        if channel_list is not None:
            try:
                channel_list.remove(session)
            except ValueError:
                pass
            if not channel_list:
                del self._by_channel[session.channel]
        session.close()
        return session

    def get(self, session_id: str) -> VoiceSession | None:
        return self._by_id.get(session_id)

    def sessions_for_channel(self, channel: str) -> list[VoiceSession]:
        """返回该 channel 上所有活跃 session（normalize channel 去 '#'）。"""
        normalized = channel.lstrip("#")
        return list(self._by_channel.get(normalized, ()))

    def all(self) -> list[VoiceSession]:
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)
