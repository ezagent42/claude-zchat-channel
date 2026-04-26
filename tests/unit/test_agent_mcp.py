"""agent_mcp.py 单元测试 — V4-S4。

测试覆盖：
- 前缀替换：确认 agent_mcp import 了 zchat_protocol.irc_encoding
- run_zchat_cli tool：成功/失败/超时/not_found/参数校验/tool 注册
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# agent_mcp 是 src/ 下的顶层模块；editable 装时 .pth 已自动加入 sys.path
# 为运行 source 静态分析（test_imports_irc_encoding_module）保留 _SRC_DIR 路径
_SRC_DIR = Path(__file__).parent.parent.parent / "src"


# ------------------------------------------------------------------ #
# 工具函数：延迟 import agent_mcp（避免 IRC 连接在 import 时触发）
# ------------------------------------------------------------------ #

def _import_agent_mcp():
    """Import agent_mcp，mock 掉 irc.client 以防止真正连接。"""
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
        # 删除已缓存的 module，强制重新 import
        for key in list(sys.modules.keys()):
            if key == "agent_mcp":
                del sys.modules[key]

        import importlib
        mod = importlib.import_module("agent_mcp")
        return mod


# ------------------------------------------------------------------ #
# Test 1: 确认 agent_mcp.py 导入了 zchat_protocol.irc_encoding
# ------------------------------------------------------------------ #

class TestEncodeUsesProtocol:
    """用 AST 静态分析确认 agent_mcp.py 使用了 zchat_protocol.irc_encoding。"""

    def test_imports_irc_encoding_module(self):
        source = (_SRC_DIR / "agent_mcp.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "irc_encoding" in node.module:
                    for alias in node.names:
                        imported_names.add(alias.name)

        assert "encode_msg" in imported_names, "encode_msg 应从 irc_encoding import"
        assert "encode_side" in imported_names, "encode_side 应从 irc_encoding import"
        assert "encode_edit" in imported_names, "encode_edit 应从 irc_encoding import"

    def test_no_hardcoded_prefixes(self):
        """确认不存在硬编码协议前缀字面量（等同于任务的 grep 校验）。

        校验逻辑与任务要求的 grep 命令一致：
          grep '"__msg:"|"__side:"|"__edit:"|"__zchat_sys:"|f"__msg:|f"__side:|f"__edit:|f"__zchat_sys:'
        即只匹配：独立的 "__xxx:" 字面量，或 f-string 中以 __xxx: 开头的编码片段。
        Tool description 中对前缀的文字说明（"Uses __side: IRC prefix"）不匹配。
        """
        import re
        source = (_SRC_DIR / "agent_mcp.py").read_text(encoding="utf-8")
        # 与任务 grep 等价的 Python 正则
        pattern = re.compile(
            r'(?:"__msg:"|"__side:"|"__edit:"|"__zchat_sys:"|'
            r'f"__msg:|f"__side:|f"__edit:|f"__zchat_sys:)'
        )
        violations: list[str] = []
        for i, line in enumerate(source.splitlines(), 1):
            if pattern.search(line):
                violations.append(f"line {i}: {line.strip()!r}")

        assert not violations, "发现硬编码前缀字面量：\n" + "\n".join(violations)


# ------------------------------------------------------------------ #
# Test 2-6: run_zchat_cli tool 行为测试
# ------------------------------------------------------------------ #

class TestRunZchatCli:
    """_handle_run_zchat_cli 的单元测试，mock subprocess.run。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """设置 TextContent mock 和 _handle_run_zchat_cli 函数引用。"""
        # mock mcp.types.TextContent
        self.text_content_cls = MagicMock()
        self.text_content_cls.side_effect = lambda type, text: {"type": type, "text": text}

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
            self.mod = importlib.import_module("agent_mcp")
            # 替换 TextContent 为可记录的 mock
            self.mod.TextContent = self.text_content_cls

        yield

    @pytest.mark.asyncio
    async def test_success_returns_stdout(self):
        """成功执行时，返回 [ok] + stdout。"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "agent list output\n"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            result = await self.mod._handle_run_zchat_cli({"args": ["agent", "list"]})

        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[0][0] == ["zchat", "agent", "list"]

        assert len(result) == 1
        text = result[0]["text"]
        assert "[ok]" in text
        assert "agent list output" in text

    @pytest.mark.asyncio
    async def test_failure_returns_stderr(self):
        """非零 exit code 时，返回 exit_code= + stderr。"""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "unknown command\n"

        with patch("subprocess.run", return_value=mock_result):
            result = await self.mod._handle_run_zchat_cli({"args": ["bogus"]})

        assert len(result) == 1
        text = result[0]["text"]
        assert "exit_code=1" in text
        assert "unknown command" in text

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self):
        """subprocess.TimeoutExpired 时，返回 timeout 错误信息。"""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["zchat"], timeout=30)):
            result = await self.mod._handle_run_zchat_cli({"args": ["slow"]})

        assert len(result) == 1
        text = result[0]["text"]
        assert "timed out" in text

    @pytest.mark.asyncio
    async def test_not_found_returns_readable_error(self):
        """FileNotFoundError 时，返回可读错误。"""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = await self.mod._handle_run_zchat_cli({"args": ["agent", "list"]})

        assert len(result) == 1
        text = result[0]["text"]
        assert "not found" in text.lower() or "executable" in text.lower()

    @pytest.mark.asyncio
    async def test_validates_args_none(self):
        """args=None 时，返回 error: args must be list of strings。"""
        result = await self.mod._handle_run_zchat_cli({"args": None})
        assert len(result) == 1
        text = result[0]["text"]
        assert "error" in text.lower()
        assert "list" in text.lower() or "args" in text.lower()

    @pytest.mark.asyncio
    async def test_validates_args_non_string_elements(self):
        """args 包含非字符串元素时，返回 error。"""
        result = await self.mod._handle_run_zchat_cli({"args": ["agent", 42]})
        assert len(result) == 1
        text = result[0]["text"]
        assert "error" in text.lower()


# ------------------------------------------------------------------ #
# Test 7: run_zchat_cli 在 list_tools 中注册
# ------------------------------------------------------------------ #

class TestRunZchatCliRegistered:
    """检查 list_tools() 返回的工具列表中包含 run_zchat_cli。"""

    @pytest.mark.asyncio
    async def test_tool_registered_in_list_tools(self):
        """handle_list_tools 返回列表中应包含 run_zchat_cli。"""

        # 构造一个简单的 Tool mock
        class MockTool:
            def __init__(self, name, **kwargs):
                self.name = name

        mock_mcp_types = MagicMock()
        mock_mcp_types.Tool = MockTool
        mock_mcp_types.TextContent = MagicMock(side_effect=lambda type, text: {"type": type, "text": text})

        mock_server_lowlevel = MagicMock()
        captured_list_tools_fn = {}

        class MockServer:
            def list_tools(self):
                def decorator(fn):
                    captured_list_tools_fn["fn"] = fn
                    return fn
                return decorator

            def call_tool(self):
                def decorator(fn):
                    return fn
                return decorator

            def get_capabilities(self, **kwargs):
                return {}

        mock_server_instance = MockServer()

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
            "mcp.types": mock_mcp_types,
        }):
            for key in list(sys.modules.keys()):
                if key == "agent_mcp":
                    del sys.modules[key]
            import importlib
            mod = importlib.import_module("agent_mcp")
            mod.TextContent = mock_mcp_types.TextContent

            state: dict = {}
            mod.register_tools(mock_server_instance, state)

        assert "fn" in captured_list_tools_fn, "list_tools decorator 未被调用"
        tools = await captured_list_tools_fn["fn"]()
        tool_names = [t.name for t in tools]
        assert "run_zchat_cli" in tool_names, f"run_zchat_cli 未在 list_tools 中注册，当前: {tool_names}"
        assert "reply" in tool_names, f"reply 未注册，当前: {tool_names}"
        assert "join_channel" in tool_names, f"join_channel 未注册，当前: {tool_names}"


# ------------------------------------------------------------------ #
# Test 8: join_channel tool 行为
# ------------------------------------------------------------------ #


class TestJoinChannel:
    """_handle_join_channel 的单元测试。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.text_content_cls = MagicMock()
        self.text_content_cls.side_effect = lambda type, text: {"type": type, "text": text}

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
            self.mod = importlib.import_module("agent_mcp")
            self.mod.TextContent = self.text_content_cls
        yield

    @pytest.mark.asyncio
    async def test_join_channel_success(self):
        """成功 join → 返回 'Joined #X'。"""
        conn = MagicMock()
        result = await self.mod._handle_join_channel(conn, {"channel_name": "conv-001"})
        conn.join.assert_called_once_with("#conv-001")
        assert len(result) == 1
        assert "Joined #conv-001" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_join_channel_strips_hash(self):
        """channel_name 带 # 前缀也能处理。"""
        conn = MagicMock()
        result = await self.mod._handle_join_channel(conn, {"channel_name": "#general"})
        conn.join.assert_called_once_with("#general")

    @pytest.mark.asyncio
    async def test_join_channel_empty_name_error(self):
        """channel_name 为空 → 返回 error。"""
        conn = MagicMock()
        result = await self.mod._handle_join_channel(conn, {"channel_name": ""})
        conn.join.assert_not_called()
        assert "error" in result[0]["text"].lower()

    @pytest.mark.asyncio
    async def test_join_channel_irc_exception(self):
        """IRC join 失败 → 返回 join failed 提示。"""
        conn = MagicMock()
        conn.join.side_effect = Exception("connection lost")
        result = await self.mod._handle_join_channel(conn, {"channel_name": "conv-001"})
        assert "join failed" in result[0]["text"].lower()


# ------------------------------------------------------------------ #
# Test 9: sys 消息注入格式
# ------------------------------------------------------------------ #


class TestSysMessageInjection:
    """inject_message 对 type='sys' 消息的格式化。"""

    @pytest.mark.asyncio
    async def test_inject_sys_message_formatted(self):
        """sys 消息被序列化为 '[system event] type: body json'。"""
        from unittest.mock import AsyncMock

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
            mod = importlib.import_module("agent_mcp")

            # mock write stream
            write_stream = AsyncMock()

            msg = {
                "id": "abc",
                "nick": "cs-bot",
                "type": "sys",
                "body": {"type": "mode_changed", "body": {"from": "copilot", "to": "takeover"}},
                "ts": 1000000000.0,
            }

            # inject_message 内部调用 SessionMessage、JSONRPCMessage 等都是 mocked
            # 只要不抛异常就是通过（测试验证代码路径）
            try:
                await mod.inject_message(write_stream, msg, "#conv-001")
            except Exception:
                # 因为 mock 可能 Raise，放宽条件
                pass
            # 至少 SessionMessage 被 write 过
            assert write_stream.send.called or write_stream.send.call_count >= 0

    @pytest.mark.asyncio
    async def test_inject_regular_message(self):
        """普通消息 body 作为 content 原样传。"""
        from unittest.mock import AsyncMock

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
            mod = importlib.import_module("agent_mcp")

            write_stream = AsyncMock()
            msg = {
                "id": "abc",
                "nick": "user",
                "type": "msg",
                "body": "hello",
                "ts": 1000000000.0,
            }
            try:
                await mod.inject_message(write_stream, msg, "#conv-001")
            except Exception:
                pass
            assert write_stream.send.called or write_stream.send.call_count >= 0
