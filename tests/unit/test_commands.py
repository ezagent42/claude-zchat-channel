import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from protocol.commands import parse_command


def test_parse_hijack():
    cmd = parse_command("/hijack")
    assert cmd.name == "hijack"


def test_parse_dispatch():
    cmd = parse_command("/dispatch feishu_oc_abc deep-agent")
    assert cmd.args["conversation_id"] == "feishu_oc_abc"
    assert cmd.args["agent_nick"] == "deep-agent"


def test_parse_assign():
    cmd = parse_command("/assign fast-agent xiaoli")
    assert cmd.args["agent_nick"] == "fast-agent"


def test_non_command():
    assert parse_command("hello") is None


def test_unknown_command():
    cmd = parse_command("/foobar")
    assert cmd.name == "unknown"
