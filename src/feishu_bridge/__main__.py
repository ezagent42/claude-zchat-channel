"""CLI 入口（V6）：

  python -m feishu_bridge --bot <name> --routing <routing.toml>

V6 推荐路径：从 routing.toml [bots."<name>"] 派生所有配置（凭证 / lazy / 自身 chat 集合）。
"""

import argparse
import logging
import sys

from feishu_bridge.bridge import FeishuBridge
from feishu_bridge.config import build_config_from_routing


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Feishu Bridge — 飞书 ↔ channel-server")
    parser.add_argument("--bot", required=True,
                        help="Bot name (must be registered in routing.toml [bots])")
    parser.add_argument("--routing", required=True,
                        help="Path to routing.toml")
    parser.add_argument("--channel-server-url", default="ws://127.0.0.1:9999",
                        help="channel-server WS URL")
    args = parser.parse_args()

    try:
        config = build_config_from_routing(
            args.routing,
            args.bot,
            channel_server_url=args.channel_server_url,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    bridge = FeishuBridge(config)
    bridge.start()


if __name__ == "__main__":
    main()
