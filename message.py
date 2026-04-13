"""
Message utilities for zchat-channel-server.
Handles mention detection and text chunking.
"""

# IRC RFC 2812: 512 bytes max per message including CR/LF.
# Reserve ~120 bytes for IRC header (:nick!user@host PRIVMSG #channel :)
# to leave ~390 bytes for the actual text payload.
MAX_MESSAGE_BYTES = 390


def detect_mention(body: str, agent_name: str) -> bool:
    """Check if a message body contains an @mention of the agent."""
    return f"@{agent_name}" in body


def clean_mention(body: str, agent_name: str) -> str:
    """Remove @mention from message body and strip whitespace."""
    return body.replace(f"@{agent_name}", "").strip()


def _sanitize_for_irc(text: str) -> str:
    """Replace newlines with spaces for IRC single-line protocol."""
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def chunk_message(text: str, max_bytes: int = MAX_MESSAGE_BYTES) -> list[str]:
    """Split a message into chunks that fit within IRC byte limits.

    Uses byte length (UTF-8) instead of character count, since IRC
    enforces a 512-byte limit per message and CJK characters are 3 bytes each.
    """
    text = _sanitize_for_irc(text)

    if len(text.encode("utf-8")) <= max_bytes:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining.encode("utf-8")) <= max_bytes:
            chunks.append(remaining)
            break

        # Find the longest prefix that fits within max_bytes (UTF-8)
        # Start from a character estimate and adjust
        estimate = max_bytes // 3  # conservative for CJK
        while len(remaining[:estimate].encode("utf-8")) < max_bytes and estimate < len(remaining):
            estimate += 1
        while len(remaining[:estimate].encode("utf-8")) > max_bytes:
            estimate -= 1

        # Try to break at a space within the safe range
        cut = remaining[:estimate].rfind(" ")
        if cut == -1 or cut < estimate // 2:
            # Hard cut at byte boundary
            cut = estimate

        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()

    return chunks
