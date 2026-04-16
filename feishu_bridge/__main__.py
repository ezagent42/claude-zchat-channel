"""CLI 入口: python -m feishu_bridge --config path/to/config.yaml"""

import argparse
import sys

from feishu_bridge.bridge import FeishuBridge
from feishu_bridge.config import load_config


def main():
    parser = argparse.ArgumentParser(description="Feishu Bridge — 飞书 ↔ channel-server")
    parser.add_argument("--config", required=True, help="YAML 配置文件路径")
    args = parser.parse_args()

    config = load_config(args.config)
    bridge = FeishuBridge(config)
    bridge.start()


if __name__ == "__main__":
    main()
