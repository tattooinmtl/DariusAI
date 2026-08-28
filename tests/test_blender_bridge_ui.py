"""The Blender bridge as the app exposes it: HTTP endpoints, the title-bar
light, the /3dgame command, the installer, and the port move that gives
8765 to Blender.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient
from mcp_fake_server import FakeMCPServer, free_port

from dariusai import blender_integration as bi
from dariusai.mcp.blender import reset_bridge
from dariusai.viz.server import create_app

PAGE = (ROOT / "src" / "dariusai" / "viz" / "static" / "index.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_bridge():
    reset_bridge()
    yield
    reset_bridge()


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "brain", project_dir=tmp_path))


# ------------------------------------------------------------------ endpoints

def test_status_is_red_when_blender_is_not_running(client):
    client.put("/api/blender/endpoint",
               json={"endpoint": f"http://127.0.0.1:{free_port()}/mcp"})
    body = client.get("/api/blender/status").json()
    assert body["state"] == "red"
    assert body["connected"] is False
    assert body["tool_count"] == 0


def test_status_goes_green_and_lists_tools_when_it_is(client):
    with FakeMCPServer() as server:
        client.put("/api/blender/endpoint", json={"endpoint": server.endpoint})
        body = client.get("/api/blender/status").json()
    assert body["state"] == "green"
    assert body["connected"] is True
    assert "game3d_health_check" in body["tools"]
    assert body["server"]["name"] == "darius-blender-mcp"


def test_the_endpoint_setting_is_persisted(client):
    with FakeMCPServer() as server:
        endpoint = server.endpoint
        client.put("/api/blender/endpoint", json={"endpoint": endpoint})
        assert client.get("/api/blender/status").json()["endpoint"] == endpoint
    # a fresh bridge for the same app must still use the saved endpoint
    reset_bridge()
    assert client.get("/api/blender/status").json()["endpoint"] == endpoint


def test_a_bad_endpoint_is_rejected(client):
    assert client.put("/api/blender/endpoint", json={"endpoint": "not-a-url"}).status_code == 400


def test_connect_reports_503_when_there_is_nothing_to_connect_to(client):
    client.put("/api/blender/endpoint",
               json={"endpoint": f"http://127.0.0.1:{free_port()}/mcp"})
    assert client.post("/api/blender/connect").status_code == 503


def test_connect_succeeds_against_a_live_server(client):
    with FakeMCPServer() as server:
        client.put("/api/blender/endpoint", json={"endpoint": server.endpoint})
        body = client.post("/api/blender/connect").json()
        assert "initialize" in server.methods()
    assert body["connected"] is True


def test_info_reports_the_install_and_the_bridge(client):
    body = client.get("/api/blender/info").json()
    assert "install" in body and "bridge" in body
    assert "found" in body["install"]
    assert body["install"]["addon_source"].endswith("darius_blender_mcp")


def test_status_can_skip_the_handshake(client):
    """The poll must be able to ask for the colour alone. Setting the
    endpoint deliberately connects straight away, so start from a bridge
    that has not."""
    with FakeMCPServer() as server:
        client.put("/api/blender/endpoint", json={"endpoint": server.endpoint})
        server.calls.clear()
        reset_bridge()

        body = client.get("/api/blender/status?handshake=false").json()
        assert body["state"] == "green"
        assert body["connected"] is False
        assert server.methods() == [], "a probe must not open a session"


# ------------------------------------------------------------------ the light

def test_the_light_is_in_the_titlebar_with_a_mark():
    titlebar = re.search(r'<div id="titlebar".*?</div>\s*<div id="menubar"', PAGE, re.S)
    assert titlebar, "titlebar not found"
    assert 'id="blenderStatus"' in titlebar.group(0)
    assert "<svg" in titlebar.group(0).split('id="blenderStatus"')[1][:600], \
        "the mark must sit next to the light"


def test_all_three_light_states_are_styled():
    for state, colour in (("red", "#f87171"), ("orange", "#fbbf24"), ("green", "#6ee7b7")):
        if state == "red":
            assert re.search(r"#blenderStatus \.dot \{[^}]*" + colour, PAGE), state
        else:
            assert re.search(r"#blenderStatus\.state-" + state + r" \.dot \{[^}]*" + colour,
                             PAGE), state


def test_the_light_polls_the_status_endpoint():
    assert '"/api/blender/status"' in PAGE
    assert "setInterval(pollBlender" in PAGE


def test_clicking_the_light_opens_the_blender_panel():
    assert re.search(r'getElementById\("blenderStatus"\)\.addEventListener\("click"', PAGE)
    assert 'openModal("blender")' in PAGE
    assert 'name === "blender"' in PAGE


def test_the_panel_can_install_launch_and_reconnect():
    panel = re.search(r"function renderBlenderModal\(.*?\n  \}\n", PAGE, re.S)
    assert panel, "renderBlenderModal not found"
    text = panel.group(0)
    for endpoint in ('"/api/blender/info"', '"/api/blender/connect"',
                     '"/api/blender/install"', '"/api/blender/launch"'):
        assert endpoint in text, endpoint


def test_the_light_does_not_drag_the_window():
    """The titlebar is pywebview's drag region; a press on the light must
    not start moving the window."""
    assert re.search(r'#titlebar \.win-btn, #btnReload, #blenderStatus', PAGE)


# ------------------------------------------------------------------ /3dgame

def test_3dgame_is_registered_with_aliases():
    from dariusai.agent.commands import REGISTRY

    assert "3dgame" in REGISTRY
    spec = REGISTRY["3dgame"]
    assert spec.handler is not None
    assert REGISTRY["blender"] is spec
    assert REGISTRY["3d"] is spec


def _run_3dgame(endpoint, tmp_path):
    from dariusai.agent.commands import REGISTRY, CommandContext
    from dariusai.brain.store import BrainStore

    store = BrainStore(tmp_path / "brain")
    store.set_setting("blender_endpoint", endpoint)
    store.set_setting("project_dir", str(ROOT))
    ctx = CommandContext(store=store, app_state=None, request_id="t", emit_log=lambda e: None)
    return REGISTRY["3dgame"].handler(ctx, [])


def test_3dgame_refuses_clearly_when_blender_is_absent(tmp_path):
    result = _run_3dgame(f"http://127.0.0.1:{free_port()}/mcp", tmp_path)
    assert result.status == "error"
    assert "not connected" in result.message
    assert "Start Blender" in result.message
    assert result.ui["status"]["state"] == "red"


def test_3dgame_connects_runs_the_health_check_and_loads_the_skill(tmp_path):
    health = {
        "status": "ok", "ready": True, "blender": "5.2.0", "engine": "CYCLES", "objects": 0,
        "palettes": ["stone", "frost"], "structures": ["house", "tower"],
        "units": ["melee", "ranged"],
    }
    with FakeMCPServer(results={"game3d_health_check": health}) as server:
        result = _run_3dgame(server.endpoint, tmp_path)

    assert result.status == "ok"
    assert "3dgame ready" in result.message
    assert "house, tower" in result.message
    assert "stone, frost" in result.message
    assert result.ui["status"]["state"] == "green"
    # and the skill itself was pulled in
    assert "3dgame" in result.message.lower()


def test_the_3dgame_skill_exists_and_is_named_for_its_folder():
    skill = ROOT / "addon" / "skills" / "gamedev" / "3dgame" / "SKILL.md"
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    assert re.search(r"^name:\s*3dgame\s*$", text, re.M)
    assert re.search(r"^description:", text, re.M)


def test_the_skill_is_free_of_the_replaced_branding():
    text = (ROOT / "addon" / "skills" / "gamedev" / "3dgame" / "SKILL.md").read_text(
        encoding="utf-8").lower()
    for word in ("dominator", "domination", "dominations"):
        assert word not in text


# ------------------------------------------------------------------ installer

def test_the_addon_source_ships_with_darius():
    source = bi.addon_source()
    assert source.is_dir()
    assert (source / "__init__.py").is_file()
    assert (source / "server" / "mcp_server.py").is_file()
    assert (source / "tools" / "game3d.py").is_file()


def test_the_addon_module_name_cannot_clobber_another_addon():
    """A user may already have an unrelated Blender MCP add-on installed;
    ours must land in its own directory."""
    assert bi.ADDON_MODULE == "darius_blender_mcp"
    assert bi.addon_source().name == "darius_blender_mcp"


def test_the_addon_declares_itself_to_blender():
    text = (bi.addon_source() / "__init__.py").read_text(encoding="utf-8")
    assert "bl_info" in text
    assert '"name": "DariusAI Blender MCP"' in text
    assert "def register()" in text and "def unregister()" in text


def test_install_reports_a_useful_error_when_blender_is_missing(monkeypatch):
    monkeypatch.setattr(bi, "find_blender", lambda: None)
    result = bi.install_addon()
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_install_copies_the_tree(tmp_path, monkeypatch):
    install = bi.BlenderInstall(executable=tmp_path / "blender.exe", version="5.2",
                                addons_dir=tmp_path / "addons")
    result = bi.install_addon(install, enable=False)
    assert result["ok"] is True
    target = tmp_path / "addons" / "darius_blender_mcp"
    assert (target / "server" / "protocol.py").is_file()
    assert (target / "game3d" / "palettes.py").is_file()
    assert not list(target.rglob("__pycache__"))


def test_reinstalling_replaces_rather_than_merges(tmp_path):
    install = bi.BlenderInstall(executable=tmp_path / "blender.exe", version="5.2",
                                addons_dir=tmp_path / "addons")
    bi.install_addon(install, enable=False)
    stale = tmp_path / "addons" / "darius_blender_mcp" / "stale_module.py"
    stale.write_text("# left over from an older build", encoding="utf-8")

    bi.install_addon(install, enable=False)
    assert not stale.exists(), "a stale module survived a reinstall"


# ------------------------------------------------------------------ the port

def test_darius_moved_off_8765_so_blender_can_have_it():
    from dariusai.mcp.blender import DEFAULT_BLENDER_ENDPOINT

    assert ":8765/mcp" in DEFAULT_BLENDER_ENDPOINT

    launcher = (ROOT / "launch.pyw").read_text(encoding="utf-8")
    assert '_start_server(app, "127.0.0.1", 8780)' in launcher
    assert "8765" not in launcher.split("_start_server(app")[1][:200]

    cli = (ROOT / "src" / "dariusai" / "cli.py").read_text(encoding="utf-8")
    assert cli.count('default=8780') == 2
    assert 'default=8765' not in cli

    window = (ROOT / "src" / "dariusai" / "viz" / "window.py").read_text(encoding="utf-8")
    assert re.search(r"port: int = 8780", window)
