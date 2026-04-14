import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from message import detect_mention, clean_mention, chunk_message


def test_detect_mention():
    assert detect_mention("@alice-agent0 hello", "alice-agent0") is True
    assert detect_mention("hello @alice-agent0", "alice-agent0") is True
    assert detect_mention("hello everyone", "alice-agent0") is False


def test_clean_mention():
    assert clean_mention("@alice-agent0 hello", "alice-agent0") == "hello"


def test_chunk_message_short():
    assert chunk_message("short") == ["short"]


def test_chunk_message_long():
    text = "a" * 5000
    chunks = chunk_message(text, max_bytes=400)
    assert len(chunks) > 1
    assert all(len(c.encode("utf-8")) <= 400 for c in chunks)


def test_chunk_message_cjk():
    """CJK characters are 3 bytes each in UTF-8."""
    text = "你好" * 200  # 400 chars = 1200 bytes
    chunks = chunk_message(text, max_bytes=390)
    assert len(chunks) > 1
    assert all(len(c.encode("utf-8")) <= 390 for c in chunks)


def test_chunk_message_strips_newlines():
    """IRC PRIVMSG does not allow newlines."""
    text = "line1\nline2\r\nline3"
    chunks = chunk_message(text)
    for chunk in chunks:
        assert "\n" not in chunk
        assert "\r" not in chunk


def test_detect_mention_with_dash_separator():
    """Agent names use - separator (IRC compliant)."""
    assert detect_mention("@alice-helper hello", "alice-helper") is True
    assert detect_mention("@alice:helper hello", "alice-helper") is False
