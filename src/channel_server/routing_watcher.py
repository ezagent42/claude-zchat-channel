"""routing.toml 文件监视器。

轮询 mtime（默认 2 秒间隔），变化时：
  1. 重新加载 routing.toml
  2. 更新 Router 的路由表
  3. 对新增 channel 调 irc_conn.join；对删除的调 irc_conn.part

错误容忍：加载失败只记录日志，不崩溃。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .routing import load as load_routing

log = logging.getLogger(__name__)


async def watch_routing(
    path: str | Path,
    router: Any,        # Router，避免循环 import
    irc_conn: Any,      # IRCConnection
    *,
    interval: float = 2.0,
) -> None:
    """持续监视 routing.toml，变化时 reload + JOIN/PART 差异 channel。"""
    p = Path(path)
    last_mtime = 0.0
    try:
        last_mtime = p.stat().st_mtime if p.exists() else 0.0
    except Exception:
        pass

    last_channels: set[str] = set(router.routing.channels.keys())

    while True:
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

        try:
            if not p.exists():
                # 文件被删 → 所有 channel 都是 stale
                if last_mtime != 0.0 or last_channels:
                    log.info("[watcher] routing.toml missing; clearing routing table")
                    from .routing import RoutingTable
                    router.update_routing(RoutingTable())
                    for ch in sorted(last_channels):
                        try:
                            irc_conn.part(f"#{ch.lstrip('#')}")
                        except Exception:
                            log.exception("[watcher] part %s failed", ch)
                    last_channels = set()
                    last_mtime = 0.0
                continue

            mtime = p.stat().st_mtime
            if mtime == last_mtime:
                continue
            last_mtime = mtime

            try:
                new_routing = load_routing(p)
            except Exception:
                log.exception("[watcher] load_routing failed; keeping old table")
                continue

            router.update_routing(new_routing)
            new_channels = set(new_routing.channels.keys())

            added = new_channels - last_channels
            removed = last_channels - new_channels

            for ch in sorted(added):
                try:
                    irc_conn.join(f"#{ch.lstrip('#')}")
                    log.info("[watcher] joined new channel #%s", ch)
                except Exception:
                    log.exception("[watcher] join #%s failed", ch)

            for ch in sorted(removed):
                try:
                    irc_conn.part(f"#{ch.lstrip('#')}")
                    log.info("[watcher] parted removed channel #%s", ch)
                except Exception:
                    log.exception("[watcher] part #%s failed", ch)

            last_channels = new_channels

            if added or removed:
                log.info(
                    "[watcher] routing reloaded: +%d channels, -%d channels",
                    len(added), len(removed),
                )
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("[watcher] unexpected error in watch loop")
