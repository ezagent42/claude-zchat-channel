# zchat-channel-server

Claude Code Channel plugin — bridges IRC messaging and Claude Code via MCP.

## Install

```bash
claude plugin install zchat-channel
```

## Usage

```bash
# Start Claude Code with the channel plugin
claude --dangerously-load-development-channels plugin:zchat-channel

# Agent joins IRC server as "agent0" (configurable via AGENT_NAME env var)
# Any IRC client can interact with the agent
```

## Environment Variables

- `AGENT_NAME` — agent identifier (default: `agent0`)
- `IRC_SERVER` — IRC server address (default: 127.0.0.1)
- `IRC_PORT` — IRC server port (default: 6667)
