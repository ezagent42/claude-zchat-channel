"""
Message utilities for zchat-channel-server.
Handles mention detection and text chunking.
"""

# Maximum message length before chunking
MAX_MESSAGE_LENGTH = 4000


def detect_mention(body: str, agent_name: str) -> bool:
    """Check if a message body contains an @mention of the agent."""
    return f"@{agent_name}" in body


def clean_mention(body: str, agent_name: str) -> str:
    """Remove @mention from message body and strip whitespace."""
    return body.replace(f"@{agent_name}", "").strip()


def chunk_message(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a message into chunks, breaking at paragraph boundaries."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # Try to break at a paragraph boundary
        cut = remaining[:max_length].rfind("\n\n")
        if cut == -1 or cut < max_length // 2:
            # Fall back to newline
            cut = remaining[:max_length].rfind("\n")
        if cut == -1 or cut < max_length // 2:
            # Fall back to space
            cut = remaining[:max_length].rfind(" ")
        if cut == -1:
            # Hard cut
            cut = max_length

        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()

    return chunks
