"""ASR 引擎抽象接口。

所有 ASR 实现（whisper.cpp / volcengine / stub）遵循此协议，voice_bridge
核心不感知具体引擎。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass
class ASRResult:
    """单次识别结果。

    Attributes:
        text: 识别文本
        is_final: 是否最终结果（False = interim/partial，仍会有后续更新）
        confidence: 置信度 0-1；未知为 None
    """
    text: str
    is_final: bool
    confidence: float | None = None


class ASREngine(Protocol):
    """流式 ASR 引擎接口。

    使用模式：
        engine = XxxASR(model_path=...)
        await engine.open()
        async for result in engine.stream(audio_chunks):
            if result.is_final:
                handle(result.text)
        await engine.close()
    """

    async def open(self) -> None:
        """Initialize engine (load model, connect cloud, etc.).

        Called once per session. Idempotent: calling twice should be a no-op
        or raise a documented error.
        """
        ...

    async def close(self) -> None:
        """Release resources. Called once per session on shutdown."""
        ...

    def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        sample_rate: int = 16000,
        encoding: str = "pcm_s16le",
    ) -> AsyncIterator[ASRResult]:
        """Streaming recognition.

        Args:
            audio: async iterator yielding raw audio chunks (arbitrary size)
            sample_rate: hz (16000 standard)
            encoding: "pcm_s16le" (signed 16-bit little-endian) default;
                      engines may support "opus" / "wav" etc.

        Yields:
            ASRResult — may include interim results before final.
            Caller should filter by is_final for definitive text.
        """
        ...
