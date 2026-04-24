"""Volcengine ASR/TTS engine 单元测试 — 配置 + 协议帧构造（不连真服务）。

集成测试（需真实 VOLC_APP_ID/ACCESS_TOKEN）放 e2e suite，本文件 unit only。
"""
from __future__ import annotations

import gzip
import json

import pytest

from voice_bridge import _volc_proto as proto
from voice_bridge.asr.volcengine import VolcengineASR, _extract_result
from voice_bridge.asr.base import ASRResult
from voice_bridge.tts.volcengine import VolcengineTTS


_GOOD_CFG = {"app_id": "test-app", "access_token": "test-token"}


# ---- ASR config ----

def test_asr_requires_app_id():
    with pytest.raises(ValueError, match="app_id"):
        VolcengineASR({"access_token": "x"})


def test_asr_requires_access_token():
    with pytest.raises(ValueError, match="access_token"):
        VolcengineASR({"app_id": "x"})


def test_asr_defaults_resource_id_and_model():
    asr = VolcengineASR(_GOOD_CFG)
    assert asr._resource_id == "volc.bigasr.sauc.duration"
    assert asr._model_name == "bigmodel"
    assert asr._language == "zh-CN"


def test_asr_overrides_from_config():
    asr = VolcengineASR({**_GOOD_CFG,
                          "language": "en-US",
                          "model_name": "custom",
                          "resource_id": "custom.id",
                          "end_window_ms": 1500,
                          "force_to_speech_ms": 200})
    assert asr._language == "en-US"
    assert asr._model_name == "custom"
    assert asr._resource_id == "custom.id"
    assert asr._end_window_ms == 1500
    assert asr._force_speech_ms == 200


def test_asr_first_payload_schema():
    asr = VolcengineASR(_GOOD_CFG)
    payload = asr._build_first_payload(sample_rate=16000)
    assert payload["user"]["uid"] == "voice_bridge"
    assert payload["audio"] == {
        "format": "pcm", "rate": 16000, "bits": 16, "channel": 1, "codec": "raw",
    }
    assert payload["request"]["model_name"] == "bigmodel"
    assert payload["request"]["language"] == "zh-CN"
    assert payload["request"]["enable_punc"] is True
    assert payload["request"]["enable_itn"] is True
    assert payload["request"]["vad"]["vad_enable"] is True
    assert payload["request"]["vad"]["end_window_size"] == 800


def test_asr_first_payload_picks_up_sample_rate():
    asr = VolcengineASR(_GOOD_CFG)
    payload = asr._build_first_payload(sample_rate=24000)
    assert payload["audio"]["rate"] == 24000


# ---- ASR _extract_result ----

def test_extract_result_returns_none_for_empty():
    assert _extract_result({}) is None
    assert _extract_result({"foo": "bar"}) is None


def test_extract_result_partial_text_no_utterances():
    data = {"payload_msg": {"result": {"text": "你"}}}
    r = _extract_result(data)
    assert r == ASRResult(text="你", is_final=False)


def test_extract_result_final_via_definite_flag():
    data = {"payload_msg": {"result": {
        "text": "你好",
        "utterances": [{"text": "你好", "definite": True}],
    }}}
    r = _extract_result(data)
    assert r is not None
    assert r.text == "你好"
    assert r.is_final is True


def test_extract_result_partial_via_no_definite():
    data = {"payload_msg": {"result": {
        "text": "你好",
        "utterances": [{"text": "你好"}],   # no definite/is_final
    }}}
    r = _extract_result(data)
    assert r is not None
    assert r.is_final is False


def test_extract_result_handles_top_level_result_key():
    """Some responses have result at top level (no payload_msg wrapper)."""
    data = {"result": {"text": "ok"}}
    r = _extract_result(data)
    assert r is not None
    assert r.text == "ok"


# ---- TTS config ----

def test_tts_requires_app_id():
    with pytest.raises(ValueError, match="app_id"):
        VolcengineTTS({"access_token": "x"})


def test_tts_requires_access_token():
    with pytest.raises(ValueError, match="access_token"):
        VolcengineTTS({"app_id": "x"})


def test_tts_defaults():
    tts = VolcengineTTS(_GOOD_CFG)
    assert tts._cluster == "volcano_tts"
    assert tts._voice_type == "BV700_streaming"
    assert tts._language == "cn"
    assert tts._sample_rate == 16000


def test_tts_output_format():
    tts = VolcengineTTS(_GOOD_CFG)
    assert tts.output_format == "pcm_s16le"
    assert tts.output_sample_rate == 16000


def test_tts_request_payload_schema():
    tts = VolcengineTTS({**_GOOD_CFG, "voice_type": "zh_male_xiaoming_emo_v2_mars_bigtts"})
    payload = tts._build_request("你好世界")
    assert payload["app"]["appid"] == "test-app"
    assert payload["app"]["token"] == "test-token"
    assert payload["app"]["cluster"] == "volcano_tts"
    assert payload["audio"]["voice_type"] == "zh_male_xiaoming_emo_v2_mars_bigtts"
    assert payload["audio"]["encoding"] == "pcm"
    assert payload["audio"]["rate"] == 16000
    assert payload["audio"]["language"] == "cn"
    assert payload["request"]["text"] == "你好世界"
    assert payload["request"]["operation"] == "submit"
    assert "reqid" in payload["request"]


def test_tts_request_with_custom_sample_rate_and_language():
    tts = VolcengineTTS({**_GOOD_CFG, "sample_rate": 24000, "language": "en"})
    payload = tts._build_request("hello")
    assert payload["audio"]["rate"] == 24000
    assert payload["audio"]["language"] == "en"


@pytest.mark.asyncio
async def test_tts_synthesize_empty_text_yields_final_chunk():
    tts = VolcengineTTS(_GOOD_CFG)
    await tts.open()
    try:
        chunks = []
        async for c in tts.synthesize(""):
            chunks.append(c)
        assert len(chunks) == 1
        assert chunks[0].audio == b""
        assert chunks[0].is_final
    finally:
        await tts.close()


# ---- engines.py factory ----

def test_factory_builds_volcengine_asr():
    from voice_bridge.engines import build_asr
    asr = build_asr("volcengine", _GOOD_CFG)
    assert isinstance(asr, VolcengineASR)


def test_factory_builds_volcengine_tts():
    from voice_bridge.engines import build_tts
    tts = build_tts("volcengine", _GOOD_CFG)
    assert isinstance(tts, VolcengineTTS)


def test_factory_volcengine_asr_missing_creds_raises():
    from voice_bridge.engines import build_asr
    with pytest.raises(ValueError):
        build_asr("volcengine", {"app_id": "x"})  # missing token


def test_factory_volcengine_tts_missing_creds_raises():
    from voice_bridge.engines import build_tts
    with pytest.raises(ValueError):
        build_tts("volcengine", {})  # missing both
