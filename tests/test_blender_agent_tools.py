"""Blender's MCP tools, exposed to the agent as ordinary Darius tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_fake_server import FakeMCPServer

from dariusai.agent.tools import ToolRegistry
from dariusai.mcp import BlenderBridge
from dariusai.mcp.blender import reset_bridge
from dariusai.mcp.registry import agent_tool_name, describe_tools, register_blender_tools


@pytest.fixture(autouse=True)
def _clean_bridge():
    reset_bridge()
    yield
    reset_bridge()


@pytest.fixture
def connected():
    with FakeMCPServer() as server:
        bridge = BlenderBridge(server.endpoint)
        bridge.connect()
        yield bridge, server


def test_generic_tools_are_namespaced_and_game3d_tools_are_not():
    """`scene_info` in a flat namespace is ambiguous; `game3d_*` already
    reads as a set and prefixing it twice helps nobody."""
    assert agent_tool_name("scene_info") == "blender_scene_info"
    assert agent_tool_name("render_image") == "blender_render_image"
    assert agent_tool_name("game3d_build_unit") == "game3d_build_unit"


def test_registering_adds_every_discovered_tool(connected):
    bridge, _ = connected
    reg = ToolRegistry()
    added = register_blender_tools(reg, bridge)

    assert set(added) == {"blender_get_addon_info", "blender_scene_info",
                          "game3d_health_check"}
    for name in added:
        assert name in reg.specs


def test_the_schema_comes_from_the_server(connected):
    bridge, _ = connected
    reg = ToolRegistry()
    register_blender_tools(reg, bridge)
    spec = reg.specs["blender_scene_info"]
    assert spec.input_schema["type"] == "object"
    assert spec.description.startswith("[Blender]")


def test_calling_a_registered_tool_reaches_the_server(connected):
    bridge, server = connected
    reg = ToolRegistry()
    register_blender_tools(reg, bridge)

    out = reg.specs["game3d_health_check"].fn()
    assert json.loads(out)["tool"] == "game3d_health_check"

    call = [c for c in server.calls if c.get("method") == "tools/call"][-1]
    assert call["params"]["name"] == "game3d_health_check"


def test_arguments_are_forwarded(connected):
    bridge, server = connected
    reg = ToolRegistry()
    register_blender_tools(reg, bridge)

    reg.specs["blender_scene_info"].fn(detail="full", depth=2)
    call = [c for c in server.calls if c.get("method") == "tools/call"][-1]
    assert call["params"]["arguments"] == {"detail": "full", "depth": 2}


def test_a_failing_tool_returns_a_message_rather_than_raising():
    """The model can recover from a sentence; an exception ends the turn."""
    with FakeMCPServer(failing_tools=["scene_info"]) as server:
        bridge = BlenderBridge(server.endpoint)
        bridge.connect()
        reg = ToolRegistry()
        register_blender_tools(reg, bridge)
        out = reg.specs["blender_scene_info"].fn()
    assert "failed" in out and "scene_info" in out


def test_a_dropped_blender_is_reported_not_raised():
    server = FakeMCPServer().start()
    bridge = BlenderBridge(server.endpoint)
    bridge.connect()
    reg = ToolRegistry()
    register_blender_tools(reg, bridge)
    server.stop()

    out = reg.specs["blender_scene_info"].fn()
    assert "failed" in out.lower() or "cannot reach" in out.lower()


def test_describe_tools_lists_both_names(connected):
    bridge, _ = connected
    described = describe_tools(bridge)
    entry = next(d for d in described if d["mcp_name"] == "scene_info")
    assert entry["name"] == "blender_scene_info"


def test_the_chat_session_attaches_blender_tools_when_it_is_up(tmp_path):
    """End of the wiring: a chat opened while Blender is connected can
    actually call it."""
    from fastapi.testclient import TestClient

    from dariusai.viz.server import create_app

    with FakeMCPServer() as server:
        app = create_app(tmp_path / "brain", project_dir=tmp_path)
        client = TestClient(app)
        client.put("/api/blender/endpoint", json={"endpoint": server.endpoint})

        source = (ROOT / "src" / "dariusai" / "viz" / "server.py").read_text(encoding="utf-8")
        assert "register_blender_tools(chat_tools, bridge, store=store)" in source
        assert client.get("/api/blender/status").json()["state"] == "green"
