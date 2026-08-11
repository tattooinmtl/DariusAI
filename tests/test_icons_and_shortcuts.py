"""The app's Windows identity: the brain icon, and the shortcuts that carry
it. Both regress silently — nothing crashes when an icon turns back into a
black square or a shortcut loses its IconLocation, you just see Python's
logo on your taskbar again — so they're pinned here.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai import os_integration as osi
from dariusai.viz.server import STATIC_DIR, create_app
from dariusai.viz.window import ICON_PATH

FAVICON = STATIC_DIR / "favicon.png"


def test_favicon_is_cut_out_not_a_black_square():
    icon = Image.open(FAVICON)
    assert icon.mode == "RGBA"
    alpha = icon.getchannel("A")
    corners = [alpha.getpixel(p) for p in [(0, 0), (icon.width - 1, 0), (0, icon.height - 1),
                                           (icon.width - 1, icon.height - 1)]]
    assert corners == [0, 0, 0, 0], "corners must be transparent, not black"
    assert alpha.getpixel((icon.width // 2, icon.height // 2)) == 255, "the brain itself must be opaque"


def test_ico_carries_every_size_windows_asks_for():
    ico = Image.open(ICON_PATH)
    # 16px is the title bar / Alt-Tab size, 256px is the desktop's largest.
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(ico.ico.sizes())


def test_tray_and_window_icons_exist():
    assert FAVICON.exists() and ICON_PATH.exists()


def test_shortcut_points_at_the_venv_and_uses_the_brain_icon(tmp_path):
    link = osi.create_shortcut(tmp_path / "DariusAI.lnk")
    assert link.exists()
    raw = link.read_bytes()
    # .lnk stores its strings as UTF-16LE; the icon path is what stops
    # Explorer falling back to the target executable's icon (Python's).
    assert str(osi.icon_path()).encode("utf-16-le") in raw
    assert osi.icon_path().name == "brain.ico"


def test_install_shortcuts_writes_both_and_reports_paths(tmp_path):
    created = osi.install_shortcuts(desktop_root=tmp_path / "desk", start_menu_root=tmp_path / "menu")
    assert [Path(p).name for p in created] == ["DariusAI.lnk", "DariusAI.lnk"]
    assert all(Path(p).exists() for p in created)


def test_shortcuts_endpoint_is_a_post_and_reports_what_it_made(tmp_path, monkeypatch):
    # Never touches the real desktop: install_shortcuts is stubbed, which is
    # the whole point of the endpoint delegating rather than shelling out.
    monkeypatch.setattr(osi, "install_shortcuts", lambda *a, **k: [r"C:\fake\DariusAI.lnk"])
    client = TestClient(create_app(tmp_path / "brain", project_dir=tmp_path))

    assert client.get("/api/shortcuts").status_code == 405  # POST only — it writes to disk
    assert client.post("/api/shortcuts").json() == {"created": [r"C:\fake\DariusAI.lnk"]}
