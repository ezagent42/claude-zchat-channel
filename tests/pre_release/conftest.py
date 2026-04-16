"""Pre-release E2E 测试 fixtures — full_stack 启动/停止全栈。"""

from __future__ import annotations

import json as _json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

# ── 路径常量 ──────────────────────────────────────────────────────────
CS_DIR = Path(__file__).resolve().parent.parent.parent  # zchat-channel-server/
ZCHAT_ROOT = CS_DIR.parent  # ~/projects/zchat/
EVIDENCE_DIR = CS_DIR / "tests" / "pre_release" / "evidence" / "screenshots"


# ── Zellij 截屏工具 ──────────────────────────────────────────────────
def _zellij_session_name() -> str | None:
    """获取当前 zchat 项目的 Zellij session 名称。"""
    r = subprocess.run(
        ["zellij", "list-sessions", "--short", "--no-formatting"],
        capture_output=True, text=True,
    )
    for line in r.stdout.splitlines():
        name = line.strip()
        if "prerelease" in name:
            return name
    return None


def capture_zellij_screenshot(step_name: str) -> None:
    """截取所有 Zellij 非插件 pane 的屏幕内容，保存为文本文件。"""
    session = _zellij_session_name()
    if not session:
        return
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    r = subprocess.run(
        ["zellij", "--session", session, "action", "list-panes", "--all", "--json"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return
    try:
        panes = _json.loads(r.stdout)
    except _json.JSONDecodeError:
        return

    for pane in panes:
        if pane.get("is_plugin"):
            continue
        pane_id = f"terminal_{pane['id']}"
        tab_name = pane.get("tab_name", "unknown")
        dump = subprocess.run(
            ["zellij", "--session", session, "action", "dump-screen",
             "--pane-id", pane_id, "--full"],
            capture_output=True, text=True,
        )
        if dump.returncode == 0 and dump.stdout.strip():
            safe_tab = tab_name.replace("/", "_").replace(" ", "_")
            fname = f"{step_name}--{safe_tab}.txt"
            (EVIDENCE_DIR / fname).write_text(dump.stdout, encoding="utf-8")


# ── feishu_config ─────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def feishu_config() -> dict:
    """加载飞书 E2E 配置文件。"""
    cfg_path = CS_DIR / "tests" / "pre_release" / "feishu-e2e-config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── feishu ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def feishu(feishu_config: dict):
    """创建 FeishuTestClient 实例。"""
    from feishu_bridge.test_client import FeishuTestClient

    app_id = os.environ.get("FEISHU_APP_ID") or feishu_config.get("feishu", {}).get(
        "app_id"
    )
    app_secret = os.environ.get("FEISHU_APP_SECRET") or feishu_config.get(
        "feishu", {}
    ).get("app_secret")

    if not app_id or not app_secret:
        pytest.skip("飞书凭证未配置")

    # 配置文件中可能是 ${FEISHU_APP_ID} 占位符，跳过
    if app_id.startswith("${") or app_secret.startswith("${"):
        pytest.skip("飞书凭证未配置（配置文件中为占位符）")

    return FeishuTestClient(app_id, app_secret)


# ── groups ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def groups(feishu_config: dict) -> dict[str, str]:
    """从配置文件提取三个群聊 chat_id。"""
    return {
        "customer_chat": feishu_config["test"]["customer_chat_id"],
        "squad_chat": feishu_config["groups"]["squad_chats"][0]["chat_id"],
        "admin_chat": feishu_config["groups"]["admin_chat_id"],
    }


# ── bridge_ws ────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def bridge_ws(full_stack, feishu_config):
    """连接 channel-server Bridge API 的 WebSocket 客户端。

    用于模拟 customer/operator/admin 发消息到 channel-server。
    （飞书 bot API 发的消息不触发 WSS 事件，无法走完整链路。
    通过 Bridge API 注入消息 → channel-server → agent → feishu_bridge → 飞书群。）
    """
    import asyncio
    import json as _json
    import websockets as _ws

    cs_url = feishu_config.get("channel_server", {}).get("url", "ws://127.0.0.1:9999")

    class BridgeWSClient:
        def __init__(self):
            self._ws = None
            self._loop = asyncio.new_event_loop()

        def connect(self):
            async def _connect():
                self._ws = await _ws.connect(cs_url)
                await self._ws.send(_json.dumps({
                    "type": "register",
                    "bridge_type": "test",
                    "instance_id": "test-client",
                    "capabilities": ["customer"],
                }))
                resp = await asyncio.wait_for(self._ws.recv(), timeout=5)
                return resp
            return self._loop.run_until_complete(_connect())

        def send(self, msg: dict):
            async def _send():
                await self._ws.send(_json.dumps(msg))
            self._loop.run_until_complete(_send())

        def customer_connect(self, conversation_id: str, customer_name: str = "TestUser"):
            self.send({
                "type": "customer_connect",
                "conversation_id": conversation_id,
                "customer": {"id": customer_name, "name": customer_name},
                "metadata": {"source": "feishu"},
            })
            # 等待 ack
            async def _recv():
                try:
                    return await asyncio.wait_for(self._ws.recv(), timeout=5)
                except Exception:
                    return None
            self._loop.run_until_complete(_recv())

        def customer_message(self, conversation_id: str, text: str, message_id: str = ""):
            msg = {
                "type": "customer_message",
                "conversation_id": conversation_id,
                "text": text,
            }
            if message_id:
                msg["message_id"] = message_id
            self.send(msg)

        def operator_message(self, conversation_id: str, text: str, operator_id: str = ""):
            self.send({
                "type": "operator_message",
                "conversation_id": conversation_id,
                "text": text,
                "operator_id": operator_id,
            })

        def operator_command(self, conversation_id: str, command: str):
            self.send({
                "type": "operator_command",
                "conversation_id": conversation_id,
                "command": command,
            })

        def admin_command(self, command: str):
            self.send({
                "type": "admin_command",
                "command": command,
            })

        def close(self):
            if self._ws:
                self._loop.run_until_complete(self._ws.close())
            self._loop.close()

    client = BridgeWSClient()
    client.connect()
    yield client
    client.close()


# ── full_stack ────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def full_stack(feishu_config: dict):
    """7 步启动全栈环境，yield 后反向拆除。

    启动顺序:
      1. zchat project use prerelease-test
      2. zchat irc daemon start
      3. channel-server (subprocess)
      4. 验证 Bridge API 端口可达
      5. zchat agent create fast-agent
      6. zchat agent create deep-agent
      7. feishu_bridge (subprocess)

    拆除顺序: 7→4（反向）
    """
    cs_proc: subprocess.Popen | None = None
    bridge_proc: subprocess.Popen | None = None

    def _run_zchat(*args: str, timeout: int = 30) -> None:
        """在 zchat 根目录执行 zchat CLI 命令。"""
        subprocess.run(
            ["uv", "run", "zchat", *args],
            cwd=str(ZCHAT_ROOT),
            check=True,
            timeout=timeout,
        )

    try:
        # 1. 切换项目（--no-attach 避免 Zellij 在 subprocess 中启动）
        _run_zchat("project", "use", "prerelease-test", "--no-attach")

        # 2. 启动 IRC daemon
        _run_zchat("irc", "daemon", "start")
        time.sleep(2)

        # 3. 启动 channel-server
        cs_env = {
            **os.environ,
            "BRIDGE_PORT": "9999",
            "IRC_SERVER": "127.0.0.1",
            "CS_ROUTING_CONFIG": str(CS_DIR / "tests" / "pre_release" / "routing.toml"),
        }
        cs_proc = subprocess.Popen(
            ["uv", "run", "zchat-channel"],
            cwd=str(CS_DIR),
            env=cs_env,
        )

        # 4. 验证 Bridge API 可达
        for attempt in range(5):
            try:
                s = socket.create_connection(("127.0.0.1", 9999), timeout=3)
                s.close()
                break
            except (ConnectionRefusedError, OSError):
                if attempt == 4:
                    raise RuntimeError(
                        "Bridge API not reachable at 127.0.0.1:9999"
                    )
                time.sleep(2)

        # 4.5 启动 WeeChat + Zellij session（agent 需要在 Zellij tab 中运行）
        _run_zchat("irc", "start")
        time.sleep(2)

        # 5. 创建 fast-agent（等待 .ready marker）
        _run_zchat("agent", "create", "fast-agent", timeout=90)

        # 6. 创建 deep-agent（等待 .ready marker）
        _run_zchat("agent", "create", "deep-agent", timeout=90)

        # 等待 agent 完全就绪（Claude Code SessionStart hook → .ready marker）
        project_dir = os.path.expanduser("~/.zchat/projects/prerelease-test")
        for agent_name in ("yaosh-fast-agent", "yaosh-deep-agent"):
            ready_path = os.path.join(project_dir, "agents", f"{agent_name}.ready")
            for i in range(30):  # 最多等 60 秒
                if os.path.exists(ready_path):
                    break
                time.sleep(2)
            else:
                print(f"[conftest] WARNING: {agent_name}.ready not found after 60s")

        # 6.5 等待 agent IRC 连接建立（.ready 只表示 Claude 启动，IRC 还需几秒）
        time.sleep(15)

        # 7. 启动 feishu_bridge（捕获日志用于调试）
        bridge_log = EVIDENCE_DIR.parent / "feishu-bridge-debug.log"
        bridge_log_f = open(bridge_log, "w")
        bridge_proc = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "-m",
                "feishu_bridge",
                "--config",
                str(CS_DIR / "tests" / "pre_release" / "feishu-e2e-config.yaml"),
            ],
            cwd=str(CS_DIR),
            stderr=bridge_log_f,
            stdout=bridge_log_f,
        )

        # 等待 feishu_bridge 连接 Bridge API（需要时间建立 WebSocket）
        time.sleep(3)

        yield {
            "cs_proc": cs_proc,
            "bridge_proc": bridge_proc,
        }

    finally:
        # ── 反向拆除 ──────────────────────────────────────────────

        # 7. 停止 feishu_bridge
        if bridge_proc is not None:
            bridge_proc.terminate()
            try:
                bridge_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                bridge_proc.kill()
        try:
            bridge_log_f.close()
        except Exception:
            pass

        # 5-6. 停止 agents
        for agent in ("fast-agent", "deep-agent"):
            try:
                _run_zchat("agent", "stop", agent)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass

        # 3. 停止 channel-server
        if cs_proc is not None:
            cs_proc.terminate()
            try:
                cs_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cs_proc.kill()

        # 2. 停止 WeeChat + IRC daemon
        try:
            _run_zchat("irc", "stop")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        try:
            _run_zchat("irc", "daemon", "stop")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
