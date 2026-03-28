---
description: "Broadcast a message to all joined IRC channels. Usage: /zchat:broadcast -t \"deploying v2.1\""
argument-hint: "--text <message> [--channels <#ch1,#ch2>]"
allowed-tools: ["mcp__zchat-channel__reply"]
---

# Broadcast to All Channels

Send a message to every IRC channel this agent has joined.

## Argument parsing

Extract from the args string:
- `--text <value>` or `-t <value>`: The message to broadcast. **Required.**
- `--channels <value>` or `-C <value>`: Optional comma-separated channel list override (e.g. `"#general,#dev"`).

If `--text` is missing, show usage:
```
Usage: /zchat:broadcast --text "deploying v2.1"
       /zchat:broadcast -t "break time" --channels "#general,#dev"
```

## Determining joined channels

If `--channels` is provided, use that list. Otherwise:

1. Check the most recent `<channel>` notifications in the conversation context for `chat_id` values starting with `#`.
2. Also check the `IRC_CHANNELS` environment variable which lists channels joined at startup.

If no channels can be determined, ask the user:
```
No channels found. Specify explicitly: /zchat:broadcast --channels "#general,#dev" --text "message"
```

## Action

For each identified channel, call the MCP tool `reply` with:
- `chat_id`: the channel name (e.g. `#general`)
- `text`: the `--text` value

After sending, confirm: `Broadcast to: #channel1, #channel2, ...`
