"""voice_bridge 的 ASR/TTS engines 基于 Doubao realtime/dialogue 的最小验证。

完整 wire-level 测试需要真服务，这里只验证：
  - 参数校验（缺凭证抛错）
  - 接口契约（open/close/stream/synthesize 形状）
  - 不会意外执行 import-time 网络调用
"""
from __future__ import annotations

import pytest

from voice_bridge.asr.volcengine import VolcengineASR
from voice_bridge.tts.volcengine import VolcengineTTS


# ── ASR ──────────────────────────────────────────────────────────────


def test_asr_rejects_missing_app_id():
    with pytest.raises(ValueError, match="app_id"):
        VolcengineASR({"access_token": "tok"})


def test_asr_rejects_missing_access_token():
    with pytest.raises(ValueError, match="access_token"):
        VolcengineASR({"app_id": "a"})


def test_asr_constructor_stores_config():
    asr = VolcengineASR({"app_id": "a", "access_token": "t", "asr_language": "en-US"})
    assert asr._config["asr_language"] == "en-US"
    assert asr._opened is False
    assert asr._client is None


@pytest.mark.asyncio
async def test_asr_close_when_never_opened_is_noop():
    asr = VolcengineASR({"app_id": "a", "access_token": "t"})
    await asr.close()  # should not raise
    assert asr._opened is False


# ── TTS ──────────────────────────────────────────────────────────────


def test_tts_rejects_missing_app_id():
    with pytest.raises(ValueError, match="app_id"):
        VolcengineTTS({"access_token": "tok"})


def test_tts_rejects_missing_access_token():
    with pytest.raises(ValueError, match="access_token"):
        VolcengineTTS({"app_id": "a"})


def test_tts_default_sample_rate_is_24000():
    """Doubao realtime/dialogue TTS 默认 24kHz；旧 v1 ws_binary 是 16kHz。"""
    tts = VolcengineTTS({"app_id": "a", "access_token": "t"})
    assert tts.output_sample_rate == 24000


def test_tts_sample_rate_overridable():
    tts = VolcengineTTS({"app_id": "a", "access_token": "t", "sample_rate": 16000})
    assert tts.output_sample_rate == 16000
    assert tts._config["sample_rate_out"] == 16000


def test_tts_output_format_pcm_s16le():
    tts = VolcengineTTS({"app_id": "a", "access_token": "t"})
    assert tts.output_format == "pcm_s16le"


@pytest.mark.asyncio
async def test_tts_synthesize_empty_text_yields_empty_final():
    tts = VolcengineTTS({"app_id": "a", "access_token": "t"})
    await tts.open()
    chunks = [c async for c in tts.synthesize("")]
    assert len(chunks) == 1
    assert chunks[0].audio == b""
    assert chunks[0].is_final is True
