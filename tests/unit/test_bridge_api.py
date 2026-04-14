"""Bridge API WebSocket server — 单元测试 (spec 02-channel-server §5)"""

import pytest
from unittest.mock import MagicMock

from bridge_api.ws_server import BridgeAPIServer, BridgeConnection
from protocol.commands import parse_command  # noqa: F401  确认依赖可导入


@pytest.fixture
def server():
    return BridgeAPIServer(conversation_manager=MagicMock(), port=0)


def test_parse_register(server):
    msg = {
        "type": "register",
        "bridge_type": "feishu",
        "instance_id": "fb-1",
        "capabilities": ["customer", "operator", "admin"],
    }
    conn = server._parse_register(msg)
    assert conn.bridge_type == "feishu"
    assert set(conn.capabilities) == {"customer", "operator", "admin"}


def test_customer_connect(server):
    msg = {
        "type": "customer_connect",
        "conversation_id": "feishu_oc_abc",
        "customer": {"id": "david", "name": "David"},
    }
    server._handle_customer_connect(msg)
    server._conversation_manager.create.assert_called_once()


def test_operator_command_hijack(server):
    msg = {
        "type": "operator_command",
        "conversation_id": "c1",
        "operator_id": "xiaoli",
        "command": "/hijack",
    }
    cmd = server._parse_operator_command(msg)
    assert cmd.name == "hijack"


def test_admin_command_status(server):
    msg = {
        "type": "admin_command",
        "admin_id": "laochen",
        "command": "/status",
    }
    cmd = server._parse_admin_command(msg)
    assert cmd.name == "status"


def test_visibility_routing_public():
    assert BridgeAPIServer.compute_visibility_targets("public") == {
        "customer",
        "operator",
        "admin",
    }


def test_visibility_routing_side():
    assert BridgeAPIServer.compute_visibility_targets("side") == {"operator", "admin"}


def test_visibility_routing_system():
    assert BridgeAPIServer.compute_visibility_targets("system") == {"operator", "admin"}


def test_register_creates_connection(server):
    msg = {
        "type": "register",
        "bridge_type": "web",
        "instance_id": "wb-1",
        "capabilities": ["customer"],
    }
    conn = server._parse_register(msg)
    assert conn.instance_id == "wb-1"
    assert conn.capabilities == ["customer"]
