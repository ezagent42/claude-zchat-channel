"""E2E: channel-server 子进程能启动、保持存活。"""

from __future__ import annotations

import time

import pytest


pytestmark = [pytest.mark.e2e]


def test_server_subprocess_alive(channel_server):
    """fixture 启动后等 2s，进程应仍然存活（未崩溃）。"""
    time.sleep(2)
    assert channel_server.poll() is None, (
        f"channel-server exited early with code={channel_server.returncode}; "
        f"stderr tail: {channel_server.stderr.read(2048) if channel_server.stderr else 'n/a'}"
    )
