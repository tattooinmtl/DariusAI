"""End-to-end against a real Blender.

Everything else stubs, replicates or mocks. This launches the actual
Blender that is installed, loads the actual add-on, and drives it with the
actual client — handshake, tool discovery, a build, and a render that has
to produce a real PNG on disk.

Skipped when Blender or the add-on is absent, so the suite still runs on a
machine without them.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_fake_server import free_port

from dariusai import blender_integration as bi
from dariusai.agent.sandbox import quiet_creationflags
from dariusai.mcp import BlenderBridge
from dariusai.mcp.client import GREEN

INSTALL = bi.find_blender()

pytestmark = [
    pytest.mark.skipif(INSTALL is None, reason="Blender is not installed"),
    pytest.mark.skipif(INSTALL is not None and not INSTALL.addon_installed(),
                       reason="the DariusAI add-on is not installed into Blender"),
]

BOOT_TIMEOUT = 120  # a cold Blender start is slow, especially the first time


@pytest.fixture(scope="module")
def blender_server():
    """A headless Blender serving the add-on on a free port.

    Not 8765: the point of the port move is that 8765 belongs to a real
    Blender session, and a test must not fight the user's own app for it.
    """
    # Copy the current add-on in first. Testing whatever happened to be
    # installed is how you end up debugging code you already changed.
    synced = bi.install_addon(INSTALL, enable=False)
    assert synced["ok"], synced

    port = free_port()
    expression = (
        "import darius_blender_mcp as d;"
        "from darius_blender_mcp.server.mcp_server import serve_background;"
        "import darius_blender_mcp.tools;"
        f"serve_background('127.0.0.1', {port}, seconds=180)"
    )
    proc = subprocess.Popen(
        [str(INSTALL.executable), "--background", "--python-expr", expression],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=quiet_creationflags(),
    )

    bridge = BlenderBridge(f"http://127.0.0.1:{port}/mcp")
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"Blender exited early:\n{proc.stdout.read()[-3000:]}")
        if bridge.status(handshake=False)["state"] == GREEN:
            break
        time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("Blender never started serving")

    yield bridge

    proc.kill()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        pass


def test_the_light_goes_green_against_real_blender(blender_server):
    status = blender_server.status()
    assert status["state"] == GREEN
    assert status["connected"] is True
    assert status["server"]["name"] == "darius-blender-mcp"


def test_the_real_tool_set_is_discovered(blender_server):
    names = blender_server.tool_names()
    for expected in ("get_addon_info", "scene_info", "create_object", "execute_python",
                     "game3d_health_check", "game3d_scene_setup", "game3d_build_structure",
                     "game3d_build_unit", "game3d_apply_palette", "game3d_render_asset",
                     "game3d_list_archetypes"):
        assert expected in names, f"{expected} missing from {names}"


def test_addon_info_reports_a_real_blender(blender_server):
    info = blender_server.call("get_addon_info")
    assert info["blender_version"][0] >= 4
    assert info["background"] is True
    assert info["name"] == "darius-blender-mcp"


def test_game3d_health_check(blender_server):
    health = blender_server.call("game3d_health_check")
    assert health["ready"] is True
    assert "stone" in health["palettes"]
    assert "house" in health["structures"]
    assert "melee" in health["units"]


def test_building_a_structure_produces_real_geometry(blender_server):
    blender_server.call("game3d_scene_setup", {"resolution": 128, "samples": 8,
                                               "engine": "BLENDER_EEVEE"})
    built = blender_server.call("game3d_build_structure",
                                {"archetype": "tower", "palette": "frost", "storeys": 3})
    assert built["archetype"] == "tower"
    assert built["palette"] == "frost"
    assert built["polygons"] > 0
    assert built["height"] > 3.0
    assert built["bounds"]["size"][2] > 3.0


def test_building_a_unit_produces_real_geometry(blender_server):
    built = blender_server.call("game3d_build_unit",
                                {"archetype": "ranged", "palette": "verdant"})
    assert built["archetype"] == "ranged"
    assert built["weapon"] == "bow"
    assert built["polygons"] > 0


def test_every_archetype_builds(blender_server):
    """A kit whose archetypes only mostly work is worse than a smaller kit."""
    blender_server.call("game3d_scene_setup", {"resolution": 64, "samples": 4,
                                               "engine": "BLENDER_EEVEE"})
    catalogue = blender_server.call("game3d_list_archetypes")
    for entry in catalogue["structures"]:
        built = blender_server.call("game3d_build_structure",
                                    {"archetype": entry["name"],
                                     "name": f"t_{entry['name']}"})
        assert built["polygons"] > 0, entry["name"]
    for entry in catalogue["units"]:
        built = blender_server.call("game3d_build_unit",
                                    {"archetype": entry["name"], "name": f"u_{entry['name']}"})
        assert built["polygons"] > 0, entry["name"]


def test_applying_a_palette_rewrites_materials(blender_server):
    result = blender_server.call("game3d_apply_palette", {"palette": "crimson"})
    assert result["palette"] == "crimson"
    assert result["materials_updated"] > 0


def test_render_writes_a_real_png(tmp_path_factory, blender_server):
    out = tmp_path_factory.mktemp("render") / "asset.png"
    blender_server.call("game3d_scene_setup", {"resolution": 128, "samples": 4,
                                               "engine": "BLENDER_EEVEE"})
    built = blender_server.call("game3d_build_structure",
                                {"archetype": "house", "palette": "timber"})
    result = blender_server.call("game3d_render_asset",
                                 {"output_path": str(out), "frame_object": built["object"]},
                                 )
    assert result["exists"] is True
    assert result["bytes"] > 0
    written = Path(result["path"])
    assert written.is_file()
    assert written.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"


def test_generic_object_tools_work(blender_server):
    blender_server.call("game3d_scene_setup", {"resolution": 64, "samples": 4,
                                               "engine": "BLENDER_EEVEE"})
    created = blender_server.call("create_object", {"kind": "cube", "name": "probe", "size": 2})
    assert created["name"] == "probe"

    fetched = blender_server.call("get_object", {"name": "probe"})
    assert fetched["polygons"] == 6

    blender_server.call("transform_object", {"name": "probe", "location": [1, 2, 3]})
    assert blender_server.call("get_object", {"name": "probe"})["location"] == [1.0, 2.0, 3.0]

    blender_server.call("set_material", {"object_name": "probe", "color": [1, 0, 0]})
    assert blender_server.call("get_object", {"name": "probe"})["materials"]

    blender_server.call("delete_object", {"name": "probe"})
    names = [o["name"] for o in blender_server.call("list_objects")["objects"]]
    assert "probe" not in names


def test_execute_python_runs_and_is_gated(blender_server):
    ran = blender_server.call("execute_python",
                              {"code": "import bpy\nresult = len(bpy.data.objects)"})
    assert ran["ok"] is True
    assert isinstance(ran["result"], int)

    blocked = blender_server.call("execute_python", {"code": "import os\nos.remove('x')"})
    assert blocked["ok"] is False
    assert blocked["rejected"] is True
