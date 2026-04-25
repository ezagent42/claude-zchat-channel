"""voice_bridge config loader — 从 JSON 文件构造 VoiceBridgeConfig。

新模型：voice_bridge 只接受 --config <path>。整个 config（含 jwt_secret +
volcengine.*  + bridge 字段）在一个文件里。zchat up 决定要不要起 voice_bridge
tab，由 main repo config.toml [voice] 控制（这个 loader 不感知）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voice_bridge.config import load_config_from_json


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_load_basic(tmp_path):
    cfg_path = _write(tmp_path / "voice.json", {
        "jwt_secret": "s" * 32,
        "listen_host": "0.0.0.0",
        "listen_port": 9999,
        "cs_url": "ws://cs.example:9000",
        "serve_static": False,
        "public_ws_url_template": "wss://voice.x/ws?t=%s",
        "asr_engine": "volcengine",
        "tts_engine": "volcengine",
        "volcengine": {
            "app_id": "abc",
            "access_token": "tok",
            "asr_language": "zh-CN",
        },
    })
    cfg = load_config_from_json(cfg_path)
    assert cfg.jwt_secret == "s" * 32
    assert cfg.listen_host == "0.0.0.0"
    assert cfg.listen_port == 9999
    assert cfg.cs_ws_url == "ws://cs.example:9000"
    assert cfg.serve_static is False
    assert cfg.public_ws_url_template == "wss://voice.x/ws?t=%s"
    assert cfg.asr_engine == "volcengine"
    assert cfg.tts_engine == "volcengine"
    # volcengine creds wired into asr_config / tts_config
    assert cfg.asr_config["app_id"] == "abc"
    assert cfg.asr_config["access_token"] == "tok"
    assert cfg.tts_config["app_id"] == "abc"


def test_minimal_loopback_config(tmp_path):
    """loopback 模式只需要 stub engine，无需 jwt 或 volcengine creds."""
    cfg_path = _write(tmp_path / "voice.json", {
        "asr_engine": "stub",
        "tts_engine": "stub",
    })
    cfg = load_config_from_json(cfg_path)
    assert cfg.asr_engine == "stub"
    assert cfg.tts_engine == "stub"
    assert cfg.jwt_secret == ""  # absent → empty


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config_from_json(tmp_path / "nope.json")


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config_from_json(p)


def test_volcengine_section_optional(tmp_path):
    cfg_path = _write(tmp_path / "voice.json", {
        "jwt_secret": "x" * 32,
        "asr_engine": "stub",
        "tts_engine": "stub",
    })
    cfg = load_config_from_json(cfg_path)
    assert cfg.asr_config == {}
    assert cfg.tts_config == {}


def test_unknown_top_level_keys_ignored(tmp_path):
    """Forward compat: 未知顶层 key 不抛错，仅 log warning."""
    cfg_path = _write(tmp_path / "voice.json", {
        "jwt_secret": "y" * 32,
        "_comment": "this should be silently ignored",
        "future_field": 42,
    })
    cfg = load_config_from_json(cfg_path)
    assert cfg.jwt_secret == "y" * 32
