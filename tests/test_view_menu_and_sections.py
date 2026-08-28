"""The View menu, and Console/Terminal as loadable sections.

Layout lives under View (Windows…, Layout Options…, Reset Layout); the
3D-scene toggles live under Settings → Preferences with the rest of the
behaviour settings. Console and Terminal are registered sections that start
outside the layout and are dragged in from View → Windows.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PAGE = (Path(__file__).resolve().parents[1] / "src" / "dariusai" / "viz" / "static" / "index.html").read_text(
    encoding="utf-8"
)


def menu_block(name: str) -> str:
    match = re.search(
        r'<div class="menu" data-menu="' + name + r'">.*?\n    </div>', PAGE, re.S
    )
    assert match, f"no {name} menu in the menubar"
    return match.group(0)


def test_view_menu_carries_the_layout_entries():
    view = menu_block("view")
    for action in ("show-windows", "show-layout-options", "reset-layout"):
        assert f'data-action="{action}"' in view, f"{action} missing from the View menu"


def test_layout_entries_left_the_settings_menu():
    settings = menu_block("settings")
    assert 'data-action="show-settings"' in settings
    assert "layout" not in settings.lower(), "layout options should now be under View"


def test_every_view_action_is_wired_to_a_handler():
    for action in ("show-windows", "show-layout-options", "reset-layout"):
        assert f'"{action}": function' in PAGE, f"{action} has no MENU_ACTIONS entry"


def test_neural_toggles_moved_into_settings():
    """They are rendered by the settings body, not the layout modal."""
    body = re.search(r"function renderSettingsBody\(.*?\n  \}", PAGE, re.S)
    assert body, "renderSettingsBody not found"
    assert "optShowLabels" in body.group(0)
    assert "optShowAnimations" in body.group(0)

    layout = re.search(r"function renderLayoutOptionsModal\(.*?\n  \}\n", PAGE, re.S)
    assert layout and "optShowAnimations" not in layout.group(0)


def test_console_and_terminal_are_registered_sections():
    assert re.search(
        r'DOCK_ORDER = \["neural", "chat", "editor", "console", "terminal"\]', PAGE
    )
    assert 'WM.createPanel("console", "Console"' in PAGE
    assert 'WM.createPanel("terminal", "Terminal"' in PAGE


def test_the_new_sections_start_outside_the_layout():
    """Adding two panels must not rearrange anyone's existing workspace."""
    match = re.search(r"DEFAULT_HIDDEN = \{([^}]*)\}", PAGE)
    assert match, "no DEFAULT_HIDDEN map"
    assert "console: true" in match.group(1)
    assert "terminal: true" in match.group(1)
    assert "neural" not in match.group(1) and "chat" not in match.group(1)


def test_windows_dialog_can_place_float_and_unplace():
    dialog = re.search(r"function renderWindowsModal\(.*?\n  \}\n", PAGE, re.S)
    assert dialog, "renderWindowsModal not found"
    text = dialog.group(0)
    assert "WM.placeSection" in text
    assert "WM.floatSection" in text
    assert "WM.unplaceSection" in text
    # the drop indicator: a cell lights up under the cursor, as the real dock
    # overlay does
    assert 'classList.toggle("hot", hit)' in text


def test_window_manager_exposes_the_section_api():
    for name in ("listSections", "placeSection", "unplaceSection", "floatSection", "onChange"):
        assert re.search(rf"\b{name}: {name}\b", PAGE), f"WM does not export {name}"


def test_console_and_terminal_talk_to_the_server():
    """Both panels are live, not placeholders."""
    assert '"/ws/events"' in PAGE
    assert '"/ws/terminal"' in PAGE
    server = (
        Path(__file__).resolve().parents[1] / "src" / "dariusai" / "viz" / "server.py"
    ).read_text(encoding="utf-8")
    assert '@app.websocket("/ws/terminal")' in server
