"""voice_bridge 运行时配置 — 单一来源：JSON 文件。

约定：voice_bridge 进程只接受 `--config <path>` (+ 可选 --loopback)。
所有字段都在 JSON 里。zchat CLI 决定要不要起 voice_bridge tab，由
main repo 的 config.toml [voice] 段控制（这个模块不感知）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class VoiceBridgeConfig:
    """所有运行时参数。"""

    # --- HTTP/WS server for browser clients ---
    listen_host: str = "127.0.0.1"
    listen_port: int = 8787
    static_dir: str = ""  # 默认用包内 static/；非空则 override
    serve_static: bool = True        # True=serve call.html fallback；False=只留 /ws + /health + /issue
                                     # （自家前端集成时关掉，404 fallback 防误访问）
    public_ws_url_template: str = "" # /issue 返回的 WS URL 模板。空则用请求的 Host 头自动拼。
                                     # 例: "wss://voice.example.com/ws?t=%s"
                                     # 公网部署务必设置（否则用 ws:// 不安全）

    # --- CS 上游 ---
    cs_ws_url: str = "ws://127.0.0.1:9999"

    # --- ASR/TTS engines ---
    asr_engine: str = "stub"  # stub | volcengine
    tts_engine: str = "stub"  # stub | volcengine
    asr_config: dict = field(default_factory=dict)
    tts_config: dict = field(default_factory=dict)

    # --- JWT / auth ---
    jwt_secret: str = ""            # 内化在 voice_bridge；空 → /issue 返回 503
    token_max_age_seconds: int = 300

    # --- audio pipeline ---
    sample_rate_in: int = 16000      # 浏览器上传采样率
    sample_rate_out: int = 16000
    audio_format: str = "pcm_s16le"

    # --- misc ---
    loopback: bool = False           # L0：跳过 CS，ASR→TTS 直回（仅 dev/test）

    # --- Path C filler（降低 phone-feel 感知延迟） ---
    # ASR final 出来后立即 TTS 一段填充语，覆盖 agent 思考 + 真答复 TTS 的
    # 1-2 秒空白。客户感知"对方在听 + 在思考"，比纯沉默好。
    filler_enabled: bool = True
    filler_phrases: list[str] = field(default_factory=lambda: [
        "嗯",
        "好的",
        "稍等一下",
        "嗯，让我看一下",
        "好，我帮您查一下",
    ])

    def static_path(self) -> Path:
        """返回前端静态文件目录。"""
        if self.static_dir:
            return Path(self.static_dir)
        return Path(__file__).parent / "static"


# 顶层支持的字段（其他 key 会被 log.warning 但不抛错，便于 forward compat）
_KNOWN_TOP_LEVEL_KEYS = {
    "jwt_secret",
    "listen_host", "listen_port", "static_dir",
    "serve_static", "public_ws_url_template",
    "cs_url",  # 写到 cfg.cs_ws_url
    "asr_engine", "tts_engine",
    "volcengine",  # nested dict for volcengine creds + config
    "filler_enabled", "filler_phrases",
    "sample_rate_in", "sample_rate_out", "audio_format",
    "token_max_age_seconds",
}


def load_config_from_json(path: Path | str) -> VoiceBridgeConfig:
    """Read voice_bridge config from a JSON file.

    Schema: see voice.json.example. 必填的只有 jwt_secret（公网部署时）；
    其余字段都有合理默认。

    Raises:
        FileNotFoundError: path doesn't exist
        ValueError: invalid JSON or wrong types
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"voice_bridge config not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"voice_bridge config is not valid JSON ({p}): {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"voice_bridge config root must be an object: {p}")

    # warn on unknown keys (forward compat — don't break)
    for key in data:
        if key not in _KNOWN_TOP_LEVEL_KEYS and not key.startswith("_"):
            log.warning("voice_bridge config: unknown key '%s' ignored", key)

    cfg = VoiceBridgeConfig()
    cfg.jwt_secret = str(data.get("jwt_secret", "")).strip()
    cfg.listen_host = str(data.get("listen_host", cfg.listen_host))
    cfg.listen_port = int(data.get("listen_port", cfg.listen_port))
    cfg.static_dir = str(data.get("static_dir", ""))
    cfg.serve_static = bool(data.get("serve_static", cfg.serve_static))
    cfg.public_ws_url_template = str(data.get("public_ws_url_template", ""))
    cfg.cs_ws_url = str(data.get("cs_url", cfg.cs_ws_url))
    cfg.asr_engine = str(data.get("asr_engine", cfg.asr_engine))
    cfg.tts_engine = str(data.get("tts_engine", cfg.tts_engine))
    cfg.token_max_age_seconds = int(data.get("token_max_age_seconds", cfg.token_max_age_seconds))
    cfg.sample_rate_in = int(data.get("sample_rate_in", cfg.sample_rate_in))
    cfg.sample_rate_out = int(data.get("sample_rate_out", cfg.sample_rate_out))
    cfg.audio_format = str(data.get("audio_format", cfg.audio_format))
    if "filler_enabled" in data:
        cfg.filler_enabled = bool(data["filler_enabled"])
    if "filler_phrases" in data and isinstance(data["filler_phrases"], list):
        cfg.filler_phrases = [str(x) for x in data["filler_phrases"] if str(x).strip()]

    # nested volcengine — wire into asr_config/tts_config based on engine
    volc = data.get("volcengine") or {}
    if isinstance(volc, dict):
        if cfg.asr_engine == "volcengine":
            cfg.asr_config = {
                "app_id": str(volc.get("app_id", "")),
                "access_token": str(volc.get("access_token", "")),
                "language": str(volc.get("asr_language", "zh-CN")),
                "resource_id": str(volc.get("asr_resource_id", "volc.bigasr.sauc.duration")),
            }
        if cfg.tts_engine == "volcengine":
            cfg.tts_config = {
                "app_id": str(volc.get("app_id", "")),
                "access_token": str(volc.get("access_token", "")),
                "cluster": str(volc.get("tts_cluster", "volcano_tts")),
                "voice_type": str(volc.get("tts_voice", "BV700_streaming")),
                "language": str(volc.get("tts_language", "cn")),
                "sample_rate": int(volc.get("tts_sample_rate", 16000)),
            }

    return cfg
