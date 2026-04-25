"""agent_mcp voice_link tool — only registered when VOICE_BRIDGE_ISSUE_URL env set.

只有 fast-agent template 启动时会注入这个 env，其他 template 不暴露此 tool。
Tool 实现：HTTP GET 到 voice_bridge /issue 拿 {url, expires_at}，返回给 agent。
"""
from __future__ import annotations

import json
import sys
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


def _import_agent_mcp_fresh():
    """Force fresh import so env var changes are reflected."""
    with patch.dict("sys.modules", {
        "irc": MagicMock(),
        "irc.client": MagicMock(),
        "irc.connection": MagicMock(),
        "anyio": MagicMock(),
        "mcp": MagicMock(),
        "mcp.server": MagicMock(),
        "mcp.server.stdio": MagicMock(),
        "mcp.server.lowlevel": MagicMock(),
        "mcp.server.models": MagicMock(),
        "mcp.shared.message": MagicMock(),
        "mcp.types": MagicMock(),
    }):
        for key in list(sys.modules.keys()):
            if key == "agent_mcp":
                del sys.modules[key]
        import importlib
        return importlib.import_module("agent_mcp")


class TestVoiceLinkHandler:
    """直接测试 _handle_voice_link 函数（不走 MCP 注册路径）。"""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        monkeypatch.setenv("VOICE_BRIDGE_ISSUE_URL", "http://127.0.0.1:8787/issue")
        self.text_content_cls = MagicMock()
        self.text_content_cls.side_effect = lambda type, text: {"type": type, "text": text}
        self.mod = _import_agent_mcp_fresh()
        self.mod.TextContent = self.text_content_cls
        yield

    @pytest.mark.asyncio
    async def test_returns_url_on_success(self):
        fake_resp = MagicMock()
        fake_resp.read.return_value = json.dumps({
            "url": "ws://127.0.0.1:8787/ws?t=eyJabc",
            "expires_at": 1700000000,
        }).encode("utf-8")
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda *a: None
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_open:
            result = await self.mod._handle_voice_link({
                "channel": "conv-001", "customer": "alice",
            })
        called_url = mock_open.call_args[0][0]
        assert "channel=conv-001" in called_url
        assert "customer=alice" in called_url
        assert "127.0.0.1:8787/issue" in called_url

        text = result[0]["text"]
        data = json.loads(text)
        assert data["url"].startswith("ws://")
        assert "t=eyJabc" in data["url"]

    @pytest.mark.asyncio
    async def test_passes_ttl_when_provided(self):
        fake_resp = MagicMock()
        fake_resp.read.return_value = b'{"url":"x","expires_at":0}'
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = lambda *a: None
        with patch("urllib.request.urlopen", return_value=fake_resp) as mock_open:
            await self.mod._handle_voice_link({
                "channel": "c", "customer": "u", "ttl_seconds": 300,
            })
        assert "ttl=300" in mock_open.call_args[0][0]

    @pytest.mark.asyncio
    async def test_missing_required_args_returns_error(self):
        result = await self.mod._handle_voice_link({"channel": "c"})
        assert "error" in result[0]["text"].lower()
        assert "customer" in result[0]["text"].lower()

    @pytest.mark.asyncio
    async def test_http_error_returns_error_text(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url", 503, "Service Unavailable", {}, None,
            ),
        ):
            result = await self.mod._handle_voice_link({
                "channel": "c", "customer": "u",
            })
        text = result[0]["text"].lower()
        assert "error" in text or "503" in text


class TestVoiceLinkRegistration:
    """list_tools 是否包含 voice_link 取决于 env。

    mcp.types.Tool 在测试环境是 MagicMock — 我们记录构造调用的 name kwarg
    来判断哪些 tool 被声明。
    """

    def _capture_tool_names(self, monkeypatch, env_set: bool):
        if env_set:
            monkeypatch.setenv("VOICE_BRIDGE_ISSUE_URL", "http://127.0.0.1:8787/issue")
        else:
            monkeypatch.delenv("VOICE_BRIDGE_ISSUE_URL", raising=False)
        mod = _import_agent_mcp_fresh()
        names: list[str] = []
        # Replace Tool with a recorder that returns an obj exposing .name
        def _recorder(**kw):
            obj = MagicMock()
            obj.name = kw.get("name", "")
            names.append(obj.name)
            return obj
        mod.Tool = _recorder
        mod._build_tool_list()
        return names

    def test_voice_link_in_tool_list_when_env_set(self, monkeypatch):
        names = self._capture_tool_names(monkeypatch, env_set=True)
        assert "voice_link" in names
        assert "reply" in names

    def test_voice_link_absent_when_env_missing(self, monkeypatch):
        names = self._capture_tool_names(monkeypatch, env_set=False)
        assert "voice_link" not in names
        assert "reply" in names
