"""A page can be newer than the Python behind it.

2026-10-09: after 0.95 was merged the window reloaded the new page, but the
server was still the 0.94 process, so replies came out as empty HTML blocks.
The page now names the version it was built for, offers Restart (not Reload)
when the server reports another, and DesktopAPI.restart() relaunches the app.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import dariusai
from dariusai.viz.window import DesktopAPI

PAGE = (ROOT / "src/dariusai/viz/static/index.html").read_text(encoding="utf-8")


def test_page_names_the_version_it_was_built_for():
    m = re.search(r'<meta name="dariusai-version" content="([^"]+)">', PAGE)
    assert m, "index.html lost its dariusai-version meta tag"
    assert m.group(1) == dariusai.__version__, "run tools/bump_version.py — it keeps the page in step"


def test_page_offers_restart_when_the_server_is_another_version():
    assert "checkServerMatchesPage(v)" in PAGE
    assert 'offerUpdate("restart", v)' in PAGE
    assert "api.restart()" in PAGE


def test_page_splits_think_tags_even_from_an_old_server():
    assert "function splitReasoning(text)" in PAGE
    assert 'appendThinkingStep("reason", split.reasoning)' in PAGE


def test_restart_launches_the_venv_pythonw_then_quits(monkeypatch):
    import subprocess

    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: launched.append(args))
    exits = []
    api = DesktopAPI(force_exit=lambda: exits.append(1), exit_grace=0.0)

    assert api.restart() is True
    assert launched, "nothing was launched"
    exe, script = launched[0]
    assert script.endswith("launch.pyw")
    assert Path(exe).name.lower() in ("pythonw.exe", "python.exe", "python")
    assert api._quitting is True          # and this instance goes through the one quit path
