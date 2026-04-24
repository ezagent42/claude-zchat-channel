"""voice_portal plugin 单元测试。"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

import pytest

from plugins.voice_portal.plugin import VoicePortalPlugin


_TEST_SECRET = "test-secret-at-least-32-bytes-long-for-hs256-happy"
_TEST_URL = "https://cs.example.com/call"


class _EmittedEvents:
    """Mock emit_event collector."""
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []  # (event, channel, data)
    async def __call__(self, event: str, channel: str, data: dict) -> None:
        self.events.append((event, channel, data))


def _make_plugin(tmp_path, monkeypatch, *, portal_url: str = _TEST_URL,
                 secret: str = _TEST_SECRET, ttl: int = 180,
                 write_creds: bool = True) -> tuple[VoicePortalPlugin, _EmittedEvents]:
    """构造 plugin：写 voice.json + 设 CS_ROUTING_CONFIG 指向 tmp_path/routing.toml。"""
    routing_toml = tmp_path / "routing.toml"
    routing_toml.write_text("", encoding="utf-8")
    monkeypatch.setenv("CS_ROUTING_CONFIG", str(routing_toml))
    creds_dir = tmp_path / "credentials"
    creds_dir.mkdir(exist_ok=True)
    if write_creds:
        (creds_dir / "voice.json").write_text(
            json.dumps({"jwt_secret": secret, "portal_url": portal_url}),
            encoding="utf-8",
        )
    emitter = _EmittedEvents()
    plugin = VoicePortalPlugin(
        config={"credentials_file": "credentials/voice.json", "ttl_seconds": ttl},
        emit_event=emitter,
    )
    return plugin, emitter


# ---- handles_commands ----

def test_handles_commands(tmp_path, monkeypatch):
    plugin, _ = _make_plugin(tmp_path, monkeypatch)
    assert plugin.handles_commands() == ["call"]


# ---- happy path ----

@pytest.mark.asyncio
async def test_call_emits_voice_url_issued_event(tmp_path, monkeypatch):
    plugin, emitter = _make_plugin(tmp_path, monkeypatch)
    await plugin.on_command("call", {
        "channel": "conv-001",
        "source": "feishu-zhangsan",
    })
    assert len(emitter.events) == 1
    event, channel, data = emitter.events[0]
    assert event == "voice_url_issued"
    assert channel == "conv-001"
    assert data["customer"] == "feishu-zhangsan"
    # URL 放在 message 字段（router._slim_for_irc 会自动截断，避免 MessageTooLong）
    assert data["message"].startswith(_TEST_URL + "?t=")  # JWT appended
    assert "url" not in data          # 不再用 url 字段
    assert data["expires_at"] > 0
    assert data["ttl_seconds"] == 180


@pytest.mark.asyncio
async def test_call_normalizes_channel_with_hash_prefix(tmp_path, monkeypatch):
    plugin, emitter = _make_plugin(tmp_path, monkeypatch)
    await plugin.on_command("call", {
        "channel": "#conv-001",
        "source": "feishu-x",
    })
    assert emitter.events[0][1] == "conv-001"  # '#' stripped


@pytest.mark.asyncio
async def test_call_with_existing_query_in_portal_url_uses_amp(tmp_path, monkeypatch):
    plugin, emitter = _make_plugin(
        tmp_path, monkeypatch, portal_url="https://cs.example.com/call?env=prod"
    )
    await plugin.on_command("call", {"channel": "c", "source": "feishu-x"})
    url = emitter.events[0][2]["message"]
    assert "?env=prod&t=" in url


@pytest.mark.asyncio
async def test_call_anonymous_when_source_internal(tmp_path, monkeypatch):
    plugin, emitter = _make_plugin(tmp_path, monkeypatch)
    await plugin.on_command("call", {"channel": "c", "source": "internal"})
    customer = emitter.events[0][2]["customer"]
    assert customer.startswith("anon-")


@pytest.mark.asyncio
async def test_call_anonymous_when_source_empty(tmp_path, monkeypatch):
    plugin, emitter = _make_plugin(tmp_path, monkeypatch)
    await plugin.on_command("call", {"channel": "c", "source": ""})
    customer = emitter.events[0][2]["customer"]
    assert customer.startswith("anon-")


# ---- voice source self-bounce protection ----

@pytest.mark.asyncio
async def test_call_from_voice_source_ignored(tmp_path, monkeypatch):
    """已经在语音上的客户再喊 /call 应该被忽略，避免无限递归。"""
    plugin, emitter = _make_plugin(tmp_path, monkeypatch)
    await plugin.on_command("call", {
        "channel": "c",
        "source": "voice-zhangsan",
    })
    assert emitter.events == []


# ---- misconfiguration paths emit voice_unavailable ----

@pytest.mark.asyncio
async def test_call_without_portal_url_emits_unavailable(tmp_path, monkeypatch):
    plugin, emitter = _make_plugin(tmp_path, monkeypatch, portal_url="")
    await plugin.on_command("call", {"channel": "c", "source": "x"})
    assert len(emitter.events) == 1
    event, _, data = emitter.events[0]
    assert event == "voice_unavailable"
    assert "portal_url" in str(data["missing"])


@pytest.mark.asyncio
async def test_call_without_jwt_secret_emits_unavailable(tmp_path, monkeypatch):
    """credentials_file 里没 jwt_secret → voice_unavailable。"""
    plugin, emitter = _make_plugin(tmp_path, monkeypatch, secret="")
    await plugin.on_command("call", {"channel": "c", "source": "x"})
    assert len(emitter.events) == 1
    event, _, data = emitter.events[0]
    assert event == "voice_unavailable"
    assert "jwt_secret" in str(data["missing"])


@pytest.mark.asyncio
async def test_call_without_credentials_file_emits_unavailable(tmp_path, monkeypatch):
    """plugins.toml 没配 credentials_file → voice_unavailable。"""
    routing_toml = tmp_path / "routing.toml"
    routing_toml.write_text("", encoding="utf-8")
    monkeypatch.setenv("CS_ROUTING_CONFIG", str(routing_toml))
    emitter = _EmittedEvents()
    plugin = VoicePortalPlugin(config={}, emit_event=emitter)
    await plugin.on_command("call", {"channel": "c", "source": "x"})
    assert len(emitter.events) == 1
    assert emitter.events[0][0] == "voice_unavailable"


# ---- TTL clamping ----

def test_ttl_clamped_to_30_900(tmp_path, monkeypatch):
    p1, _ = _make_plugin(tmp_path, monkeypatch, ttl=10)
    assert p1._ttl == 30
    p2, _ = _make_plugin(tmp_path, monkeypatch, ttl=99999)
    assert p2._ttl == 900
    p3, _ = _make_plugin(tmp_path, monkeypatch, ttl=200)
    assert p3._ttl == 200


# ---- token verifies (round-trip with bridge tokens module) ----

@pytest.mark.asyncio
async def test_emitted_url_token_validates(tmp_path, monkeypatch):
    """Plugin 签发的 token 用 bridge 的 JWTValidator 能验过。"""
    from voice_bridge.tokens import JWTValidator
    plugin, emitter = _make_plugin(tmp_path, monkeypatch, ttl=300)
    await plugin.on_command("call", {"channel": "conv-001", "source": "feishu-zhang"})
    url = emitter.events[0][2]["message"]
    token = url.split("?t=")[-1].split("&")[0]
    validator = JWTValidator(secret=_TEST_SECRET)
    claims = validator.validate(token)
    assert claims is not None
    assert claims["channel"] == "conv-001"
    assert claims["customer"] == "feishu-zhang"
