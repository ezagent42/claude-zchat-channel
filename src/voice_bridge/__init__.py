"""voice_bridge — zchat 实时语音桥接。

作为独立 bridge 进程，与 feishu_bridge 同级，通过 WebSocket 连 channel_server
广播。对外暴露一个 WS 端点给浏览器客户端连接（`/call` 页面）。

架构立场（详见 docs/discuss/013-voice-overlay.md）：
- voice 是 channel 上的一次性 overlay，不是独立实体
- routing.toml 零改动 — voice session 纯 in-memory
- 单 bridge 进程 N:1 session→IRC channel
- ASR/TTS 引擎 pluggable（abstract base + engine 子类）
"""

__version__ = "0.1.0-dev0"
