"""voice_bridge 运行时配置。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VoiceBridgeConfig:
    """所有运行时参数。

    多数字段有 env 兜底；命令行 / config.toml 可覆盖。
    """

    # --- HTTP/WS server for browser clients ---
    listen_host: str = "127.0.0.1"
    listen_port: int = 8787
    static_dir: str = ""  # 默认用包内 static/；非空则 override

    # --- CS 上游 ---
    cs_ws_url: str = "ws://127.0.0.1:9999"

    # --- ASR/TTS engines ---
    asr_engine: str = "stub"  # stub | whisper_cpp | volcengine
    tts_engine: str = "stub"  # stub | piper | edge_tts
    asr_config: dict = field(default_factory=dict)
    tts_config: dict = field(default_factory=dict)

    # --- JWT / auth ---
    jwt_secret: str = ""            # Phase 3 启用；为空 → dev-mode only
    dev_mode: bool = True            # 允许 URL 直接 ?channel=...&customer=...
    token_max_age_seconds: int = 300

    # --- audio pipeline ---
    sample_rate_in: int = 16000      # 浏览器上传采样率
    sample_rate_out: int = 16000
    audio_format: str = "pcm_s16le"

    # --- misc ---
    loopback: bool = False           # L0：跳过 CS，ASR→TTS 直回
    bind_channel: str = ""           # 固定绑死一个 channel（dev mode）；为空则靠 token 解

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

    @classmethod
    def from_env(cls) -> "VoiceBridgeConfig":
        """从环境变量构造（用于 python -m voice_bridge 默认值）。"""
        return cls(
            listen_host=os.environ.get("VOICE_BRIDGE_HOST", "127.0.0.1"),
            listen_port=int(os.environ.get("VOICE_BRIDGE_PORT", "8787")),
            cs_ws_url=os.environ.get("VOICE_BRIDGE_CS_URL", "ws://127.0.0.1:9999"),
            asr_engine=os.environ.get("VOICE_BRIDGE_ASR", "stub"),
            tts_engine=os.environ.get("VOICE_BRIDGE_TTS", "stub"),
            jwt_secret=os.environ.get("VOICE_BRIDGE_JWT_SECRET", ""),
            dev_mode=os.environ.get("VOICE_BRIDGE_DEV_MODE", "1") == "1",
            loopback=os.environ.get("VOICE_BRIDGE_LOOPBACK", "0") == "1",
            bind_channel=os.environ.get("VOICE_BRIDGE_CHANNEL", ""),
        )

    def static_path(self) -> Path:
        """返回前端静态文件目录。"""
        if self.static_dir:
            return Path(self.static_dir)
        return Path(__file__).parent / "static"
