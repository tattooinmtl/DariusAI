"""The Blender add-on's bpy-free layers.

The protocol dispatcher, the code gate and the palette data are deliberately
free of `bpy` so they can be tested here rather than only inside Blender.
They are loaded straight off disk: importing the add-on package would pull
in `bpy` through its `__init__`, and stubbing Blender to test a pure
function is a poor trade.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon" / "blender" / "darius_blender_mcp"
sys.path.insert(0, str(ROOT / "src"))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ADDON / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


protocol = _load("_darius_protocol", "server/protocol.py")
safe_eval = _load("_darius_safe_eval", "utils/safe_eval.py")
palettes = _load("_darius_palettes", "game3d/palettes.py")


@pytest.fixture
def registry():
    reg = protocol.ToolRegistry()

    @reg.tool(name="echo", description="echo it back",
              input_schema={"type": "object", "properties": {"value": {"type": "string"}}})
    def echo(value: str = ""):
        return {"echoed": value}

    @reg.tool(name="boom", description="always fails")
    def boom():
        raise RuntimeError("it broke")

    return reg


def rpc(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "method": method, "id": request_id}
    if params is not None:
        message["params"] = params
    return message


# ---------------------------------------------------------------- registry

def test_the_decorator_registers_a_tool(registry):
    assert registry.names() == ["boom", "echo"]
    assert len(registry) == 2


def test_tools_list_is_mcp_shaped(registry):
    tools = registry.list_mcp()
    assert {t["name"] for t in tools} == {"echo", "boom"}
    entry = next(t for t in tools if t["name"] == "echo")
    assert entry["description"] == "echo it back"
    assert entry["inputSchema"]["type"] == "object"


def test_a_tool_without_a_description_falls_back_to_its_docstring():
    reg = protocol.ToolRegistry()

    @reg.tool(name="documented")
    def documented():
        """What the docstring says."""

    assert reg.list_mcp()[0]["description"] == "What the docstring says."


# ---------------------------------------------------------------- dispatch

def test_initialize_answers_with_the_protocol_and_server_identity(registry):
    response = protocol.dispatch(rpc("initialize"), registry)
    result = response["result"]
    assert response["jsonrpc"] == "2.0" and response["id"] == 1
    assert result["protocolVersion"] == protocol.PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "darius-blender-mcp"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert "game3d" in result["instructions"]


def test_tools_list_and_call(registry):
    listed = protocol.dispatch(rpc("tools/list"), registry)["result"]["tools"]
    assert len(listed) == 2

    called = protocol.dispatch(
        rpc("tools/call", {"name": "echo", "arguments": {"value": "hi"}}), registry)["result"]
    assert called["isError"] is False
    assert called["content"][0]["text"] == '{"echoed": "hi"}'


def test_ping(registry):
    assert protocol.dispatch(rpc("ping"), registry)["result"] == {}


def test_a_notification_gets_no_response(registry):
    """No id means no reply. Answering one would put an unexpected message
    on the wire that the client has to discard."""
    assert protocol.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"},
                             registry) is None


def test_unknown_method_is_an_rpc_error(registry):
    response = protocol.dispatch(rpc("no/such/method"), registry)
    assert response["error"]["code"] == protocol.METHOD_NOT_FOUND


def test_unknown_tool_is_an_rpc_error(registry):
    response = protocol.dispatch(rpc("tools/call", {"name": "ghost"}), registry)
    assert response["error"]["code"] == protocol.INVALID_PARAMS
    assert "unknown tool" in response["error"]["message"]


def test_bad_arguments_are_an_rpc_error_not_a_crash(registry):
    response = protocol.dispatch(
        rpc("tools/call", {"name": "echo", "arguments": {"nope": 1}}), registry)
    assert response["error"]["code"] == protocol.INVALID_PARAMS


def test_arguments_must_be_an_object(registry):
    response = protocol.dispatch(
        rpc("tools/call", {"name": "echo", "arguments": [1, 2]}), registry)
    assert response["error"]["code"] == protocol.INVALID_PARAMS


def test_a_failing_tool_reports_iserror_and_keeps_the_server_alive(registry):
    """A tool that raises must come back as a readable result — killing
    the connection would take Blender's whole bridge down with it."""
    result = protocol.dispatch(rpc("tools/call", {"name": "boom"}), registry)["result"]
    assert result["isError"] is True
    assert "it broke" in result["content"][0]["text"]


def test_a_non_jsonrpc_message_is_rejected(registry):
    assert protocol.dispatch({"method": "initialize", "id": 1}, registry)["error"]["code"] == \
        protocol.INVALID_REQUEST


def test_dispatch_uses_the_supplied_invoker(registry):
    """Inside Blender the invoker marshals onto the main thread; the
    dispatcher must route every call through it."""
    seen = []

    def invoker(tool, arguments):
        seen.append((tool.name, arguments))
        return {"via": "invoker"}

    protocol.dispatch(rpc("tools/call", {"name": "echo", "arguments": {"value": "x"}}),
                      registry, invoker)
    assert seen == [("echo", {"value": "x"})]


# ---------------------------------------------------------------- health

def test_health_payload_matches_what_the_client_requires(registry):
    """The status-light contract. `MCPClient.health()` reads exactly these
    keys, so the two sides are pinned to each other here."""
    from dariusai.mcp.client import GREEN, MCPClient  # noqa: PLC0415

    payload = protocol.health_payload(registry)
    assert set(payload) >= {"status", "server", "version", "protocolVersion", "tools_count"}
    assert payload["status"] == "ok"
    assert payload["tools_count"] == 2

    # And it is genuinely green when served to the real client.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mcp_fake_server import FakeMCPServer  # noqa: PLC0415

    with FakeMCPServer(health_payload=payload) as server:
        state, detail = MCPClient(server.endpoint).health()
    assert state == GREEN
    assert "darius-blender-mcp" in detail


# ---------------------------------------------------------------- code gate

@pytest.mark.parametrize("source", [
    "import bpy\nresult = 1 + 1",
    "import math\nresult = math.pi",
    "from mathutils import Vector\nresult = 1",
    "result = [x * 2 for x in range(4)]",
])
def test_ordinary_blender_code_passes(source):
    safe_eval.check(source)


@pytest.mark.parametrize("source,reason", [
    ("import os", "os"),
    ("import subprocess", "subprocess"),
    ("from os import path", "os"),
    ("__import__('os')", "__import__"),
    ("open('/etc/passwd')", "open"),
    ("eval('1')", "eval"),
    ("exec('x=1')", "exec"),
    # ast.walk reaches the outer attribute first, so the message names
    # whichever link in the chain it hit — any of them is a rejection.
    ("().__class__.__bases__", "__bases__"),
    ("x = (1).__class__.__subclasses__()", "__subclasses__"),
    ("import shutil\nshutil.rmtree('/')", "shutil"),
])
def test_escapes_are_rejected(source, reason):
    with pytest.raises(safe_eval.UnsafeCode) as exc:
        safe_eval.check(source)
    assert reason in str(exc.value)


def test_empty_and_oversized_code_is_rejected():
    with pytest.raises(safe_eval.UnsafeCode, match="empty"):
        safe_eval.check("   ")
    with pytest.raises(safe_eval.UnsafeCode, match="limit"):
        safe_eval.check("x = 1\n" * 40_000)


def test_a_syntax_error_names_its_line():
    with pytest.raises(safe_eval.UnsafeCode, match="line 2"):
        safe_eval.check("x = 1\ndef (:")


def test_run_executes_and_returns_result():
    assert safe_eval.run("result = 6 * 7")["result"] == 42


def test_the_namespace_is_trimmed_as_well_as_gated():
    """Belt and braces: the AST gate rejects `__import__` before it runs,
    and the namespace it would have run in does not contain it either."""
    with pytest.raises(safe_eval.UnsafeCode):
        safe_eval.run("result = __import__")

    builtins = safe_eval.safe_globals()["__builtins__"]
    for escape in ("eval", "exec", "compile", "open", "globals"):
        assert escape not in builtins, f"{escape} is reachable from executed code"
    assert "len" in builtins and "range" in builtins


def test_import_is_allowed_but_only_for_the_allow_list():
    """`import bpy` is the point of the tool, so __import__ has to exist —
    a trimmed __builtins__ with none at all makes every import statement
    fail. It is replaced with a guarded one rather than removed."""
    guarded = safe_eval.safe_globals()["__builtins__"]["__import__"]
    assert guarded("math").pi > 3

    with pytest.raises(ImportError, match="not allowed"):
        guarded("os")
    with pytest.raises(ImportError, match="not allowed"):
        guarded("subprocess")


def test_allowed_imports_actually_execute():
    assert safe_eval.run("import math\nresult = math.floor(3.7)")["result"] == 3


# ---------------------------------------------------------------- palettes

def test_every_palette_defines_every_role():
    for name, palette in palettes.PALETTES.items():
        assert set(palette) == set(palettes.ROLES), f"{name} is missing roles"
        for role, colour in palette.items():
            assert len(colour) == 3, f"{name}.{role}"
            assert all(0.0 <= c <= 1.0 for c in colour), f"{name}.{role} out of range"


def test_palette_names_are_neutral():
    """No factions, no borrowed branding — the point of replacing the
    dominator kit."""
    forbidden = {"domination", "dominations", "dominator", "viking", "roman", "greek",
                 "egyptian", "japanese", "persian", "british", "chinese"}
    assert not (set(palettes.palette_names()) & forbidden)


def test_unknown_palette_falls_back_rather_than_raising():
    assert palettes.get_palette("not-a-palette") is palettes.PALETTES["stone"]
    assert palettes.get_palette(None) is palettes.PALETTES["stone"]
    assert palettes.describe("nonsense")["palette"] == "stone"


def test_rgba_appends_alpha():
    assert palettes.rgba((0.1, 0.2, 0.3)) == (0.1, 0.2, 0.3, 1.0)


def test_every_role_has_a_surface_definition():
    assert set(palettes.SURFACE) == set(palettes.ROLES)
