---
description: "Send a private message to an IRC user. Usage: /zchat:dm -u alice -t \"hey\""
argument-hint: "--user <nick> --text <message>"
allowed-tools: ["mcp__zchat-channel__reply"]
---

# Direct Message

Parse the arguments and call the `reply` MCP tool with a user nick as target.

## Argument parsing

Extract from the args string:
- `--user <value>` or `-u <value>`: The recipient IRC nick. **Required.**
- `--text <value>` or `-t <value>`: The message text. **Required.**

If either argument is missing, show usage:
```
Usage: /zchat:dm --user alice --text "hey there"
       /zchat:dm -u bob -t "check this out"
```

## Action

Call the MCP tool `reply` with:
- `chat_id`: the `--user` value (nick, no `#` prefix)
- `text`: the `--text` value

After sending, confirm: `DM sent to <user>`.
