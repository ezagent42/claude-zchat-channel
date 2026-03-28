---
description: "Reply to an IRC channel or user. Usage: /zchat:reply -c #general -t \"hello world\""
argument-hint: "--channel <#channel|nick> --text <message>"
allowed-tools: ["mcp__zchat-channel__reply"]
---

# Reply to IRC

Parse the arguments and call the `reply` MCP tool.

## Argument parsing

Extract from the args string:
- `--channel <value>` or `-c <value>`: The target channel (e.g. `#general`) or user nick. **Required.**
- `--text <value>` or `-t <value>`: The message text. **Required.** If the value contains spaces, it may be quoted or may be everything after the flag until the next flag or end of string.

If either argument is missing, tell the user the correct usage:
```
Usage: /zchat:reply --channel #general --text "hello world"
       /zchat:reply -c alice -t "hey there"
```

## Action

Call the MCP tool `reply` with:
- `chat_id`: the `--channel` value (keep the `#` prefix for channels)
- `text`: the `--text` value

After sending, confirm: `Sent to <chat_id>`.
