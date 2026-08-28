"""Regression guards for the two things the frameless window gets wrong the
moment the page's markup drifts: an OS window nobody can move, and a Settings
panel that closes the instant you click somewhere else to fetch a key.

These assert on the shipped index.html rather than on rendered behaviour —
there's no browser in the test env — but they pin the exact hooks the fixes
depend on, which is what regressed before.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.viz.server import STATIC_DIR
from dariusai.viz.window import launch  # noqa: F401  (import smoke-checks the module)

PAGE = (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def test_titlebar_opts_into_pywebviews_drag_region():
    """WebView2 ignores `-webkit-app-region: drag`, so the titlebar has to
    carry pywebview's own class or the window can't be moved at all."""
    titlebar = re.search(r'<div id="titlebar"[^>]*>', PAGE)
    assert titlebar, "titlebar element missing"
    assert "pywebview-drag-region" in titlebar.group(0)


def test_window_buttons_do_not_start_a_window_drag():
    """pywebview's drag listener sits on <body> and walks up to the drag
    region, so the min/max/close buttons must stop the press first."""
    assert "#titlebar .win-btn" in PAGE
    assert "ev.stopPropagation()" in PAGE


def test_settings_panel_is_modeless():
    # Matched loosely: the invariant is that settings is in the modeless set,
    # not that it is the only entry — pinning the exact literal broke as soon
    # as New Project and Windows joined it.
    assert re.search(r"MODELESS = \{[^}]*\bsettings: true", PAGE)
    assert "#modalRoot.modeless { background: none; pointer-events: none; }" in PAGE


def test_click_outside_never_closes_the_modeless_panel():
    guard = re.search(r'if \(ev\.target\.id === "modalRoot"[^\n]*', PAGE)
    assert guard, "backdrop click handler missing"
    assert 'classList.contains("modeless")' in guard.group(0)
