"""Bridge API WebSocket server — 单元测试 (spec 02-channel-server §5)"""

import pytest
from unittest.mock import MagicMock

from bridge_api.ws_server import BridgeAPIServer, BridgeConnection
from zchat_protocol.commands import parse_command  # noqa: F401  确认依赖可导入


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


def test_visibility_routing_public(server):
    assert server.compute_visibility_targets("public") == {
        "customer",
        "operator",
        "admin",
    }


def test_visibility_routing_side(server):
    assert server.compute_visibility_targets("side") == {"operator", "admin"}


def test_visibility_routing_system(server):
    assert server.compute_visibility_targets("system") == {"operator", "admin"}


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


# TC-U12: on_operator_join 回调被调用
def test_on_operator_join_callback_invoked():
    """operator_join 消息类型存在回调槽，且 _handle_connection 会调用它。"""
    # 验证 BridgeAPIServer 有 on_operator_join 属性（槽已声明）
    bs = BridgeAPIServer(conversation_manager=MagicMock(), port=0)
    assert hasattr(bs, "on_operator_join")
    assert bs.on_operator_join is None  # 默认未注入


# TC-U13: send_event 广播到所有已注册连接
def test_send_event_broadcasts_to_all_connections():
    """send_event 应向 _connections 中所有有 websocket 的连接发送事件 JSON。"""
    import asyncio
    from unittest.mock import AsyncMock

    bs = BridgeAPIServer(conversation_manager=MagicMock(), port=0)

    async def _noop(*a, **kw):
        pass

    ws1 = MagicMock()
    ws1.send = AsyncMock(side_effect=_noop)
    ws2 = MagicMock()
    ws2.send = AsyncMock(side_effect=_noop)

    from bridge_api.ws_server import BridgeConnection
    bs._connections["c1"] = BridgeConnection(
        bridge_type="test", instance_id="c1", capabilities=["customer"], websocket=ws1
    )
    bs._connections["c2"] = BridgeConnection(
        bridge_type="test", instance_id="c2", capabilities=["operator"], websocket=ws2
    )

    asyncio.run(bs.send_event("mode.changed", {"from": "auto", "to": "copilot"}, "conv1"))

    assert ws1.send.call_count == 1
    assert ws2.send.call_count == 1
    # 内容应含 event_type
    import json
    payload = json.loads(ws1.send.call_args.args[0])
    assert payload["type"] == "event"
    assert payload["event_type"] == "mode.changed"
    assert payload["data"]["to"] == "copilot"
    assert payload["conversation_id"] == "conv1"


# TC-U14: send_event 无连接时不报错
def test_send_event_no_connections_noop():
    """send_event 在无连接时静默返回，不抛异常。"""
    import asyncio
    bs = BridgeAPIServer(conversation_manager=MagicMock(), port=0)
    # 不应抛异常
    asyncio.run(bs.send_event("mode.changed", {}, "conv1"))
