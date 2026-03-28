---
description: "Join an IRC channel. Usage: /zchat:join -c dev"
argument-hint: "--channel <channel-name>"
allowed-tools: ["mcp__zchat-channel__join_channel"]
---

# Join IRC Channel

Parse the arguments and call the `join_channel` MCP tool.

## Argument parsing

Extract from the args string:
- `--channel <value>` or `-c <value>`: Channel name to join. **Required.** Strip any leading `#` — the MCP tool adds it.

If missing, show usage:
```
Usage: /zchat:join --channel dev
       /zchat:join -c general
```

## Action

Call the MCP tool `join_channel` with:
- `channel_name`: the `--channel` value with any `#` prefix stripped

After joining, confirm: `Joined #<channel>`.
