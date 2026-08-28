"""The MCP client and the Blender bridge.

The traffic light is the reason `health()` is separate from the handshake,
so the three colours are pinned here against real sockets: nothing
listening, something wrong on the port, and a healthy server.
"""

from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_fake_server import FakeMCPServer, free_port

from dariusai.mcp import BlenderBridge, MCPClient, MCPError
from dariusai.mcp.blender import DEFAULT_BLENDER_ENDPOINT, get_bridge, reset_bridge
from dariusai.mcp.client import GREEN, ORANGE, RED, data_of, text_of


@pytest.fixture
def server():
    with FakeMCPServer() as s:
        yield s


@pytest.fixture(autouse=True)
def _clean_bridge():
    reset_bridge()
    yield
    reset_bridge()


# ---------------------------------------------------------------- handshake

def test_handshake_runs_the_full_mcp_sequence(server):
    client = MCPClient(server.endpoint)
    result = client.connect()

    assert result["protocolVersion"] == "2024-11-05"
    assert client.server_info["name"] == "darius-blender-mcp"
    # initialize, then the required notification, then discovery
    assert server.methods() == ["initialize", "notifications/initialized", "tools/list"]


def test_initialize_announces_darius_as_the_client(server):
    MCPClient(server.endpoint).initialize()
    init = server.calls[0]
    assert init["params"]["clientInfo"]["name"] == "dariusai"
    assert init["jsonrpc"] == "2.0" and "id" in init


def test_the_initialized_notification_carries_no_id(server):
    """A notification with an id is a request, and a server may wait to
    answer one that never gets answered."""
    MCPClient(server.endpoint).initialize()
    note = server.calls[1]
    assert note["method"] == "notifications/initialized"
    assert "id" not in note


def test_tools_are_discovered(server):
    client = MCPClient(server.endpoint)
    client.connect()
    assert [t.name for t in client.tools] == [
        "get_addon_info", "scene_info", "game3d_health_check"]
    assert client.tools[0].input_schema["type"] == "object"


# ---------------------------------------------------------------- tool calls

def test_call_tool_round_trips_arguments(server):
    client = MCPClient(server.endpoint)
    client.connect()
    payload = data_of(client.call_tool("scene_info", {"detail": "full"}))
    assert payload == {"tool": "scene_info", "arguments": {"detail": "full"}}


def test_unknown_tool_raises(server):
    client = MCPClient(server.endpoint)
    client.connect()
    with pytest.raises(MCPError, match="unknown tool"):
        client.call_tool("nope")


def test_a_tool_reporting_iserror_raises_with_its_message():
    with FakeMCPServer(failing_tools=["scene_info"]) as s:
        client = MCPClient(s.endpoint)
        client.connect()
        with pytest.raises(MCPError, match="scene_info blew up"):
            client.call_tool("scene_info")


def test_ping(server):
    assert MCPClient(server.endpoint).ping() is True


def test_text_and_data_helpers():
    result = {"content": [{"type": "text", "text": '{"a": 1}'}]}
    assert text_of(result) == '{"a": 1}'
    assert data_of(result) == {"a": 1}
    assert data_of({"content": [{"type": "text", "text": "plain"}]}) == "plain"


# ---------------------------------------------------------------- the light

def test_green_when_a_healthy_server_answers(server):
    state, detail = MCPClient(server.endpoint).health()
    assert state == GREEN
    assert "darius-blender-mcp" in detail and "3 tools" in detail


def test_red_when_nothing_is_listening():
    state, detail = MCPClient(f"http://127.0.0.1:{free_port()}/mcp").health()
    assert state == RED
    assert "nothing listening" in detail


def test_orange_when_the_wrong_server_holds_the_port():
    """The exact misconfiguration a port clash produces: something is
    there, it just isn't an MCP server."""
    with FakeMCPServer(health_non_json=True) as s:
        state, detail = MCPClient(s.endpoint).health()
    assert state == ORANGE
    assert "not an MCP server" in detail


def test_orange_when_the_server_reports_unhealthy():
    with FakeMCPServer(status="degraded") as s:
        state, detail = MCPClient(s.endpoint).health()
    assert state == ORANGE
    assert "degraded" in detail


def test_orange_when_the_health_payload_is_not_mcp_shaped():
    with FakeMCPServer(health_payload={"hello": "world"}) as s:
        state, _ = MCPClient(s.endpoint).health()
    assert state == ORANGE


def test_orange_on_an_http_error():
    with FakeMCPServer(health_status_code=500) as s:
        state, detail = MCPClient(s.endpoint).health()
    assert state == ORANGE
    assert "500" in detail


def test_orange_when_the_port_is_open_but_silent():
    """A socket that accepts and never replies must not read as healthy."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    threading.Thread(target=lambda: None, daemon=True).start()
    try:
        state, detail = MCPClient(f"http://127.0.0.1:{port}/mcp").health(read_timeout=0.6)
    finally:
        listener.close()
    assert state == ORANGE
    assert "never answered" in detail or "failed mid-request" in detail


# ---------------------------------------------------------------- the bridge

def test_bridge_handshakes_on_first_healthy_probe(server):
    bridge = BlenderBridge(server.endpoint)
    assert bridge.connected is False

    status = bridge.status()
    assert status["state"] == GREEN
    assert status["connected"] is True
    assert status["tool_count"] == 3
    assert "game3d_health_check" in status["tools"]
    assert server.methods().count("initialize") == 1


def test_bridge_does_not_re_handshake_on_every_poll(server):
    bridge = BlenderBridge(server.endpoint)
    for _ in range(4):
        bridge.status()
    assert server.methods().count("initialize") == 1


def test_bridge_reports_red_and_forgets_the_handshake_when_blender_goes_away():
    s = FakeMCPServer().start()
    bridge = BlenderBridge(s.endpoint)
    assert bridge.status()["connected"] is True
    s.stop()

    status = bridge.status()
    assert status["state"] == RED
    assert status["connected"] is False


def test_bridge_reconnects_when_blender_comes_back():
    port = free_port()
    bridge = BlenderBridge(f"http://127.0.0.1:{port}/mcp")
    assert bridge.status()["state"] == RED

    server = FakeMCPServer()
    server._server = None
    # bind the same port the bridge is pointed at
    from http.server import HTTPServer  # noqa: PLC0415

    import mcp_fake_server as fake  # noqa: PLC0415
    srv = fake._Server(("127.0.0.1", port), fake._Handler)
    srv.config, srv.calls = server.config, []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    assert isinstance(srv, HTTPServer)
    try:
        status = bridge.status()
        assert status["state"] == GREEN
        assert status["connected"] is True
    finally:
        srv.shutdown()
        srv.server_close()


def test_bridge_call_connects_first(server):
    bridge = BlenderBridge(server.endpoint)
    payload = bridge.call("scene_info", {"x": 1})
    assert payload == {"tool": "scene_info", "arguments": {"x": 1}}
    assert bridge.connected is True


def test_bridge_call_drops_the_handshake_on_failure(server):
    bridge = BlenderBridge(server.endpoint)
    bridge.connect()
    with pytest.raises(MCPError):
        bridge.call("does_not_exist")
    assert bridge.connected is False


def test_configure_repoints_and_drops_state(server):
    bridge = BlenderBridge(server.endpoint)
    bridge.status()
    assert bridge.connected is True

    bridge.configure(endpoint=f"http://127.0.0.1:{free_port()}/mcp")
    assert bridge.connected is False
    assert bridge.tool_names() == []
    assert bridge.status()["state"] == RED


def test_the_default_endpoint_is_8765():
    """The port the add-on listens on, and the reason DariusAI's own
    server moved to 8780."""
    assert DEFAULT_BLENDER_ENDPOINT == "http://127.0.0.1:8765/mcp"


def test_get_bridge_is_a_singleton(server):
    first = get_bridge(server.endpoint)
    assert get_bridge() is first
    assert get_bridge(server.endpoint) is first
