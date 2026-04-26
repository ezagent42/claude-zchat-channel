#!/usr/bin/env python3
"""zchat agent MCP server — 轻量 MCP stdio 代理.

每个 Claude Code agent 运行一个 agent_mcp 实例。
职责：MCP tools (reply/run_zchat_cli) + IRC @mention 注入。
"""
from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any

import anyio
import irc.client
import mcp.server.stdio
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCNotification, TextContent, Tool

from zchat_protocol.irc_encoding import encode_edit, encode_msg, encode_side

# ------------------------------------------------------------------ #
# 消息工具
# ------------------------------------------------------------------ #

# IRC RFC 2812: 512 bytes max。保留 IRC 头部后，payload 上限约 390 bytes
_MAX_MESSAGE_BYTES = 390


def _sanitize_for_irc(text: str) -> str:
    """IRC 单行协议：替换换行符为空格。"""
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def chunk_message(text: str, max_bytes: int = _MAX_MESSAGE_BYTES) -> list[str]:
    """按 UTF-8 字节数拆分消息，符合 IRC RFC 2812 限制。"""
    text = _sanitize_for_irc(text)
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining.encode("utf-8")) <= max_bytes:
            chunks.append(remaining)
            break
        estimate = max_bytes // 3
        while len(remaining[:estimate].encode("utf-8")) < max_bytes and estimate < len(remaining):
            estimate += 1
        while len(remaining[:estimate].encode("utf-8")) > max_bytes:
            estimate -= 1
        cut = remaining[:estimate].rfind(" ")
        if cut == -1 or cut < estimate // 2:
            cut = estimate
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    return chunks


def detect_mention(body: str, agent_name: str) -> bool:
    """检测消息是否包含 @mention。"""
    return f"@{agent_name}" in body


def clean_mention(body: str, agent_name: str) -> str:
    """移除 @mention 并去除首尾空白。"""
    return body.replace(f"@{agent_name}", "").strip()


# ------------------------------------------------------------------ #
# 环境变量
# ------------------------------------------------------------------ #

AGENT_NAME = os.environ.get("AGENT_NAME", "agent0")
IRC_SERVER = os.environ.get("IRC_SERVER", "127.0.0.1")
IRC_PORT = int(os.environ.get("IRC_PORT", "6667"))
IRC_CHANNELS = os.environ.get("IRC_CHANNELS", "general")
IRC_TLS = os.environ.get("IRC_TLS", "false").lower() == "true"
IRC_AUTH_TOKEN = os.environ.get("IRC_AUTH_TOKEN", "")

# ------------------------------------------------------------------ #
# MCP notification 注入
# ------------------------------------------------------------------ #


async def inject_message(write_stream, msg: dict, context: str) -> None:
    """将 IRC 侧消息以 MCP notification 注入 Claude Code。

    msg["type"] == "sys" 时 body 是 dict（来自 __zchat_sys: payload），
    序列化为 "[system event] <sys_type>: <json>" 格式，方便 Claude 识别。
    """
    msg_type = msg.get("type", "msg")
    ts = msg.get("ts", 0)
    iso_ts = (
        datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        if ts
        else datetime.now(tz=timezone.utc).isoformat()
    )

    body = msg.get("body", "")
    if msg_type == "sys":
        # sys event: body 是 dict，序列化成可读字符串
        import json as _json
        sys_type = body.get("type") if isinstance(body, dict) else "unknown"
        data = body.get("body") if isinstance(body, dict) else {}
        content = f"[system event] {sys_type}: {_json.dumps(data, ensure_ascii=False)}"
    else:
        content = body

    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/claude/channel",
        params={
            "content": content,
            "meta": {
                "chat_id": context,
                "message_id": msg.get("id", ""),
                "user": msg.get("nick", "unknown"),
                "ts": iso_ts,
                "type": msg_type,
            },
        },
    )
    await write_stream.send(SessionMessage(message=JSONRPCMessage(notification)))


async def poll_irc_queue(queue: asyncio.Queue, write_stream) -> None:
    """将 IRC 队列中的消息持续注入到 MCP 客户端。"""
    while True:
        msg, context = await queue.get()
        try:
            await inject_message(write_stream, msg, context)
        except Exception as e:
            print(f"[agent-mcp] inject error: {e}", file=sys.stderr)


# ------------------------------------------------------------------ #
# MCP Server 构造 + Tool 注册
# ------------------------------------------------------------------ #


def load_instructions(agent_name: str) -> str:
    path = Path(__file__).parent / "instructions.md"
    tmpl = Template(path.read_text(encoding="utf-8"))
    return tmpl.safe_substitute(agent_name=agent_name)


def create_server() -> Server:
    instructions = load_instructions(AGENT_NAME)
    return Server("zchat-agent-mcp", instructions=instructions)


def register_tools(server: Server, state: dict) -> None:
    """注册 MCP tools。IRC 连接从 state 字典懒解析。"""

    def _get_irc():
        conn = state.get("irc_connection")
        if conn is None:
            raise RuntimeError("IRC connection not initialized yet")
        return conn

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        return _build_tool_list()

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "reply":
            return await _handle_reply(_get_irc(), arguments)
        if name == "join_channel":
            return await _handle_join_channel(_get_irc(), arguments)
        if name == "run_zchat_cli":
            return await _handle_run_zchat_cli(arguments)
        if name == "list_peers":
            return await _handle_list_peers(state.get("members") or {}, arguments)
        if name == "voice_link":
            return await _handle_voice_link(arguments)
        raise ValueError(f"Unknown tool: {name}")


# ------------------------------------------------------------------ #
# Tool list — voice_link 仅当 VOICE_BRIDGE_ISSUE_URL 存在时暴露
# ------------------------------------------------------------------ #


def _build_tool_list() -> list[Tool]:
    """Construct the tool list visible to Claude.

    voice_link 是按需注册：只有 fast-agent template 启动时被注入
    VOICE_BRIDGE_ISSUE_URL env，此 tool 才进入列表。其他 template 看不到。
    """
    tools: list[Tool] = [
        Tool(
            name="reply",
            description=(
                "Send a message to a channel or user via IRC.\n\n"
                "Parameters:\n"
                "- chat_id (required): Target channel (#channel) or username for DM\n"
                "- text (required): Message content\n"
                "- edit_of (optional): message_id of a previous message to replace\n"
                "- side (optional): If true, message is only visible to operators\n\n"
                "Returns JSON with message_id and sent_to.\n\n"
                "Usage patterns:\n"
                "- Public message: reply(chat_id='#conv-001', text='hello')\n"
                "- Edit/replace: reply(chat_id='#conv-001', text='corrected', edit_of='<message_id>')\n"
                "- Side message: reply(chat_id='#conv-001', text='internal note', side=true)\n"
                "- Plugin command: reply(chat_id='#conv-001', text='/hijack')\n\n"
                "Available plugin commands (via text):\n"
                "- /hijack → switch channel to takeover mode (operator drives)\n"
                "- /release → switch channel back to copilot mode (agent drives)\n"
                "- /copilot → same as /release\n"
                "- /resolve → mark conversation as resolved"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "string",
                        "description": "Target: #channel (e.g. '#conv-001', '#general') or username for DM",
                    },
                    "text": {
                        "type": "string",
                        "description": "Message content, or /command to trigger a plugin",
                    },
                    "edit_of": {
                        "type": "string",
                        "description": "message_id of a previous message to edit/replace",
                    },
                    "side": {
                        "type": "boolean",
                        "description": "If true, only visible to operators (not customers)",
                    },
                },
                "required": ["chat_id", "text"],
            },
        ),
        Tool(
            name="join_channel",
            description=(
                "Join an IRC channel to receive @mentions.\n\n"
                "Parameters:\n"
                "- channel_name (required): Channel name without # prefix\n\n"
                "Usage: dynamically join a new IRC channel at runtime (e.g. when "
                "dispatched to a new conversation). The MCP server sends IRC JOIN "
                "and starts listening for @mentions in the channel."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channel_name": {
                        "type": "string",
                        "description": "Channel name without # prefix (e.g. 'conv-001')",
                    },
                },
                "required": ["channel_name"],
            },
        ),
        Tool(
            name="list_peers",
            description=(
                "List other agent nicks currently joined to a given IRC channel.\n\n"
                "Returns: JSON list[str] of nicks (excluding self and well-known service "
                "nicks like cs-bot). Useful before delegating: discover who else is in your "
                "channel and pick a peer by naming convention (e.g. matches '*-deep-*').\n\n"
                "Parameters:\n"
                "- channel (required): channel name with or without leading '#'\n\n"
                "Returns empty list if you haven't joined that channel or no peers present."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name (e.g. 'conv-001' or '#conv-001')",
                    },
                },
                "required": ["channel"],
            },
        ),
        Tool(
            name="run_zchat_cli",
            description=(
                "Execute a zchat CLI command and return stdout/stderr.\n\n"
                "Parameters:\n"
                "- args (required): List of CLI arguments (excluding 'zchat' itself)\n"
                "- timeout (optional): Max seconds to wait, default 30\n\n"
                "Available commands:\n"
                "  Agent management:\n"
                "  - ['agent', 'create', '<name>'] → create agent (add '--type', '<template>' for specific type)\n"
                "  - ['agent', 'stop', '<name>'] → stop agent\n"
                "  - ['agent', 'restart', '<name>'] → restart agent\n"
                "  - ['agent', 'list'] → list all agents with status\n"
                "  - ['agent', 'status', '<name>'] → show agent details\n"
                "  - ['agent', 'send', '<name>', '<message>'] → send message to agent\n"
                "  - ['agent', 'join', '<name>', '<channel>'] → assign agent to channel\n\n"
                "  Channel management:\n"
                "  - ['channel', 'create', '<name>'] → register channel in routing.toml\n"
                "  - ['channel', 'list'] → list all registered channels\n\n"
                "  Project management:\n"
                "  - ['project', 'list'] → list projects\n"
                "  - ['project', 'show'] → show current project\n\n"
                "  IRC:\n"
                "  - ['irc', 'daemon', 'start'] → start ergo IRC server\n"
                "  - ['irc', 'daemon', 'stop'] → stop ergo IRC server\n\n"
                "  System:\n"
                "  - ['doctor'] → check environment and dependencies\n"
                "  - ['shutdown'] → stop all agents and services"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CLI arguments excluding 'zchat' itself",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Max seconds to wait (default 30)",
                        "default": 30,
                    },
                },
                "required": ["args"],
            },
        ),
    ]
    if os.environ.get("VOICE_BRIDGE_ISSUE_URL"):
        tools.append(Tool(
            name="voice_link",
            description=(
                "Generate a voice-call URL for a customer in a specific channel.\n\n"
                "Use when the customer asks to talk on the phone / voice / call. The returned URL "
                "is short-lived (default 3 min); send it to the customer via reply() and they tap "
                "to start the call. Audio is converted to text and arrives in the same channel as "
                "their normal chat.\n\n"
                "Parameters:\n"
                "- channel (required): Channel name (with or without '#') to bind the call to\n"
                "- customer (required): External customer identifier (e.g. feishu user open_id)\n"
                "- ttl_seconds (optional): URL lifetime, 30-900, default 180\n\n"
                "Returns JSON {url, expires_at}. If voice service is unavailable returns {error}.\n\n"
                "Typical flow:\n"
                "  1. customer: '能打电话吗？'\n"
                "  2. you: voice_link(channel='#conv-001', customer='ou_xxx')\n"
                "  3. you: reply(chat_id='#conv-001', text='请点击通话：<url>')"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "customer": {"type": "string"},
                    "ttl_seconds": {"type": "integer", "minimum": 30, "maximum": 900, "default": 180},
                },
                "required": ["channel", "customer"],
            },
        ))
    return tools


# ------------------------------------------------------------------ #
# Tool handlers
# ------------------------------------------------------------------ #


async def _handle_reply(connection, arguments: dict) -> list[TextContent]:
    chat_id = arguments["chat_id"]
    text = arguments["text"]
    edit_of = arguments.get("edit_of")
    side = arguments.get("side", False)
    message_id = str(uuid.uuid4())

    if edit_of:
        # 编辑替换：__edit:<original_msg_id>:<new_text>
        prefixed = encode_edit(edit_of, text)
    elif side:
        # side channel：__side:<text>
        prefixed = encode_side(text)
    else:
        # 普通消息：__msg:<msg_id>:<text>
        prefixed = encode_msg(message_id, text)

    for chunk in chunk_message(prefixed):
        connection.privmsg(chat_id, chunk)
    return [TextContent(type="text", text=f'{{"message_id": "{message_id}", "sent_to": "{chat_id}"}}')]


async def _handle_join_channel(connection, arguments: dict) -> list[TextContent]:
    """JOIN an IRC channel to receive @mentions."""
    channel = arguments.get("channel_name", "").lstrip("#")
    if not channel:
        return [TextContent(type="text", text="error: channel_name required")]
    try:
        connection.join(f"#{channel}")
    except Exception as e:
        return [TextContent(type="text", text=f"join failed: {e}")]
    return [TextContent(type="text", text=f"Joined #{channel}")]


# Service nicks excluded from list_peers (they are bridge / channel-server hosts,
# not deployable peer agents). Add new service nicks here if introduced.
_SERVICE_NICKS = {"cs-bot"}


async def _handle_list_peers(members: dict[str, set[str]], arguments: dict) -> list[TextContent]:
    """Return JSON list of peer agent nicks in a channel (excludes self + service nicks)."""
    import json as _json
    channel = arguments.get("channel", "").strip()
    if not channel:
        return [TextContent(type="text", text="error: channel required")]
    key = channel if channel.startswith("#") else f"#{channel}"
    nicks = set(members.get(key, set()))
    nicks.discard(AGENT_NAME)
    nicks -= _SERVICE_NICKS
    return [TextContent(type="text", text=_json.dumps(sorted(nicks)))]


async def _handle_run_zchat_cli(arguments: dict) -> list[TextContent]:
    """执行 zchat CLI 命令，返回 stdout/stderr。admin-agent 用于翻译 IM 命令。"""
    args = arguments.get("args")
    timeout = arguments.get("timeout", 30)
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return [TextContent(type="text", text="error: args must be list of strings")]

    cmd = ["zchat"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout if result.returncode == 0 else (result.stderr or result.stdout or "(no output)")
        status = "ok" if result.returncode == 0 else f"exit_code={result.returncode}"
        return [TextContent(
            type="text",
            text=f"[{status}] {' '.join(shlex.quote(x) for x in cmd)}\n{output}",
        )]
    except subprocess.TimeoutExpired:
        return [TextContent(type="text", text=f"error: command timed out after {timeout}s")]
    except FileNotFoundError:
        return [TextContent(type="text", text="error: 'zchat' executable not found in PATH")]
    except Exception as e:
        return [TextContent(type="text", text=f"error: {e}")]


async def _handle_voice_link(arguments: dict) -> list[TextContent]:
    """voice_link tool — HTTP GET voice_bridge /issue 拿一次性 URL。

    secret 完全在 voice_bridge 内部，agent 不持有任何 secret material。
    """
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    issue_url = os.environ.get("VOICE_BRIDGE_ISSUE_URL", "").strip()
    if not issue_url:
        return [TextContent(type="text", text='{"error":"voice not configured (VOICE_BRIDGE_ISSUE_URL unset)"}')]
    channel = str(arguments.get("channel", "")).strip()
    customer = str(arguments.get("customer", "")).strip()
    if not channel or not customer:
        return [TextContent(type="text", text='{"error":"channel and customer are required"}')]
    qs = {"channel": channel.lstrip("#"), "customer": customer}
    ttl = arguments.get("ttl_seconds")
    if ttl is not None:
        try:
            qs["ttl"] = str(int(ttl))
        except (TypeError, ValueError):
            pass
    full_url = f"{issue_url}?{urllib.parse.urlencode(qs)}"
    try:
        with urllib.request.urlopen(full_url, timeout=3) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return [TextContent(type="text", text=_json.dumps({
            "error": f"voice_bridge returned HTTP {e.code}",
            "detail": e.reason,
        }))]
    except Exception as e:
        return [TextContent(type="text", text=_json.dumps({
            "error": f"voice_bridge unreachable: {e}",
        }))]
    return [TextContent(type="text", text=body.strip())]


# ------------------------------------------------------------------ #
# main
# ------------------------------------------------------------------ #


def _start_irc(
    server: str,
    port: int,
    nick: str,
    channels: list[str],
    tls: bool,
    auth_token: str,
    queue: "asyncio.Queue",
    loop: asyncio.AbstractEventLoop,
    on_pubmsg_fn: Any,
    on_privmsg_fn: Any,
    members: dict[str, set[str]] | None = None,
) -> tuple[Any, Any]:
    """在独立线程启动 IRC reactor，返回 (reactor, connection)。

    members 字典 caller 维护引用，按 IRC 事件实时更新：channel name (含 '#') → set[nick]。
    用于 list_peers MCP tool 查询同 channel 成员，零额外 IRC roundtrip。
    """
    import functools
    import ssl
    import threading

    import irc.connection

    members_map: dict[str, set[str]] = members if members is not None else {}
    reactor = irc.client.Reactor()

    connect_kwargs: dict = {}
    if tls:
        ctx = ssl.create_default_context()
        wrapper = functools.partial(ctx.wrap_socket, server_hostname=server)
        connect_kwargs["connect_factory"] = irc.connection.Factory(wrapper=wrapper)
    if auth_token:
        connect_kwargs["sasl_login"] = nick
        connect_kwargs["password"] = auth_token

    connection = reactor.server().connect(server, port, nick, **connect_kwargs)

    def _pubmsg_handler(conn, event):
        if event.source.nick == nick:
            return
        try:
            on_pubmsg_fn(conn, event)
        except Exception as e:
            print(f"[agent-mcp] pubmsg handler error: {e}", file=sys.stderr)

    def _privmsg_handler(conn, event):
        sender = event.source.nick
        if sender == nick:
            return
        body = event.arguments[0]
        try:
            on_privmsg_fn(sender, body)
        except Exception as e:
            print(f"[agent-mcp] privmsg handler error: {e}", file=sys.stderr)

    def _on_welcome(conn, event):
        if conn.real_nickname != nick:
            print(
                f"[agent-mcp] WARNING: nick mismatch! expected={nick} actual={conn.real_nickname}",
                file=sys.stderr,
            )
        for ch in channels:
            ch_clean = ch.strip().lstrip("#")
            if ch_clean:
                conn.join(f"#{ch_clean}")
                print(f"[agent-mcp] Joined #{ch_clean}", file=sys.stderr)
        print(f"[agent-mcp] {nick} ready on IRC ({server}:{port})", file=sys.stderr)

    # ---- channel membership tracking (for list_peers MCP tool) ----
    def _on_namreply(conn, event):
        # event.arguments: ['=', '#chan', 'nick1 nick2 nick3 ...']
        args = event.arguments
        if len(args) < 3:
            return
        ch = args[1]
        for raw_nick in args[2].split():
            clean = raw_nick.lstrip("@+%&~")
            if clean:
                members_map.setdefault(ch, set()).add(clean)

    def _on_join(conn, event):
        ch = event.target
        joiner = event.source.nick if event.source else None
        if ch and joiner:
            members_map.setdefault(ch, set()).add(joiner)

    def _on_part(conn, event):
        ch = event.target
        leaver = event.source.nick if event.source else None
        if ch and leaver and ch in members_map:
            members_map[ch].discard(leaver)

    def _on_quit(conn, event):
        leaver = event.source.nick if event.source else None
        if not leaver:
            return
        for nicks in members_map.values():
            nicks.discard(leaver)

    def _on_nick(conn, event):
        old = event.source.nick if event.source else None
        new = event.target if event.target else None
        if not (old and new):
            return
        for nicks in members_map.values():
            if old in nicks:
                nicks.discard(old)
                nicks.add(new)

    connection.add_global_handler("welcome", _on_welcome)
    connection.add_global_handler("pubmsg", _pubmsg_handler)
    connection.add_global_handler("privmsg", _privmsg_handler)
    connection.add_global_handler("namreply", _on_namreply)
    connection.add_global_handler("join", _on_join)
    connection.add_global_handler("part", _on_part)
    connection.add_global_handler("quit", _on_quit)
    connection.add_global_handler("nick", _on_nick)

    def irc_thread():
        try:
            reactor.process_forever()
        except Exception as e:
            print(f"[agent-mcp] IRC reactor error: {e}", file=sys.stderr)

    t = threading.Thread(target=irc_thread, daemon=True)
    t.start()
    return reactor, connection


async def main() -> None:
    """启动 agent MCP server：MCP stdio + IRC @mention 注入。"""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    channels = [c.strip() for c in IRC_CHANNELS.split(",") if c.strip()]

    server = create_server()
    state: dict = {}
    register_tools(server, state)

    def _on_pubmsg(conn, event):
        from zchat_protocol import irc_encoding

        nick = event.source.nick
        body = event.arguments[0]
        channel = event.target

        parsed = irc_encoding.parse(body)
        kind = parsed.get("kind")

        # __zchat_sys: 系统事件 → 注入 Claude 作为 system notification
        if kind == "sys":
            payload = parsed.get("payload", {})
            msg = {
                "id": os.urandom(4).hex(),
                "nick": nick,
                "type": "sys",
                "body": payload,
                "ts": time.time(),
            }
            print(f"[agent-mcp] [{channel}] SYS event: {payload.get('type')}", file=sys.stderr)
            loop.call_soon_threadsafe(queue.put_nowait, (msg, channel))
            return

        # 普通消息：必须有 @mention 才触发
        if not detect_mention(body, AGENT_NAME):
            return
        cleaned = clean_mention(body, AGENT_NAME)
        msg = {
            "id": os.urandom(4).hex(),
            "nick": nick,
            "type": "msg",
            "body": cleaned,
            "ts": time.time(),
        }
        print(f"[agent-mcp] [{channel}] {nick}: {body}", file=sys.stderr)
        loop.call_soon_threadsafe(queue.put_nowait, (msg, channel))

    def _on_privmsg(nick: str, body: str):
        # PRIVMSG 格式: "[conv_id] sender: text"
        # 提取 conv_id 作为 chat_id
        actual_body = body
        conv_id = ""
        if body.startswith("[") and "] " in body:
            bracket_end = body.index("] ")
            conv_id = body[1:bracket_end]
            actual_body = body[bracket_end + 2:]
        context = nick
        msg = {
            "id": os.urandom(4).hex(),
            "nick": nick,
            "type": "msg",
            "body": actual_body,
            "ts": time.time(),
        }
        print(f"[agent-mcp] [private:{nick} conv={conv_id}] {actual_body}", file=sys.stderr)
        loop.call_soon_threadsafe(queue.put_nowait, (msg, context))

    members_map: dict[str, set[str]] = {}
    irc_reactor, irc_connection = _start_irc(
        IRC_SERVER, IRC_PORT, AGENT_NAME, channels,
        IRC_TLS, IRC_AUTH_TOKEN,
        queue, loop, _on_pubmsg, _on_privmsg,
        members=members_map,
    )
    state["irc_connection"] = irc_connection
    state["members"] = members_map

    init_opts = InitializationOptions(
        server_name=f"zchat-agent-{AGENT_NAME}",
        server_version="1.0.0",
        capabilities=server.get_capabilities(
            notification_options=NotificationOptions(),
            experimental_capabilities={"claude/channel": {}},
        ),
    )

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await anyio.sleep(2)
        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(server.run, read_stream, write_stream, init_opts)
                tg.start_soon(poll_irc_queue, queue, write_stream)
        finally:
            try:
                irc_connection.disconnect("Agent shutting down")
            except Exception:
                pass


def entry_point() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    entry_point()
