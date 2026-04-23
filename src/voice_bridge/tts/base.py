"""TTS 引擎抽象接口。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass
class TTSChunk:
    """单块合成音频。

    Attributes:
        audio: 原始音频字节（格式由 engine 定，见 TTSEngine.output_format）
        is_final: 是否本次合成的最后一块
    """
    audio: bytes
    is_final: bool


class TTSEngine(Protocol):
    """流式 TTS 引擎接口。

    使用模式：
        engine = XxxTTS(voice="...")
        await engine.open()
        async for chunk in engine.synthesize("你好"):
            send_to_browser(chunk.audio)
        await engine.close()
    """

    @property
    def output_format(self) -> str:
        """音频输出格式：'pcm_s16le' / 'opus' / 'mp3' / ..."""
        ...

    @property
    def output_sample_rate(self) -> int:
        """输出采样率 (hz)。"""
        ...

    async def open(self) -> None:
        """Load voice model / auth cloud engine."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...

    def synthesize(
        self,
        text: str,
    ) -> AsyncIterator[TTSChunk]:
        """Streaming synthesis.

        Args:
            text: 待合成的文本

        Yields:
            TTSChunk — 可能多块，最后一块 is_final=True
        """
        ...
