"""No child process may pop a console window onto the desktop.

The app runs under pythonw.exe, which has no console of its own. Windows
therefore allocates a fresh console for every console-mode child, and it
flashes open and shut on the user's screen. Redirecting the child's streams
does not prevent this — only CREATE_NO_WINDOW does.

This bit hardest at startup: runtime detection probes eleven interpreters
in a row, so launching the app strobed eleven console windows across the
desktop. Any new subprocess call must go through quiet_creationflags(), and
this test fails if one doesn't.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dariusai.agent.sandbox import CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, quiet_creationflags

SOURCES = sorted((ROOT / "src").rglob("*.py")) + [ROOT / "launch.pyw"]
SPAWN_RE = re.compile(r"subprocess\.(run|Popen|call|check_output|check_call)\s*\(", re.M)


@pytest.mark.skipif(sys.platform != "win32", reason="console windows are a Windows problem")
def test_the_flag_is_the_real_win32_constant():
    assert CREATE_NO_WINDOW == 0x08000000
    assert quiet_creationflags() & CREATE_NO_WINDOW
    # combining with other creation flags must not drop either
    combined = quiet_creationflags(CREATE_NEW_PROCESS_GROUP)
    assert combined & CREATE_NO_WINDOW and combined & CREATE_NEW_PROCESS_GROUP


def test_every_spawn_site_suppresses_its_console():
    """Walks the source rather than trusting a list — a subprocess call
    added later is exactly how this regresses."""
    offenders = []
    for path in SOURCES:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in SPAWN_RE.finditer(text):
            # the call's argument list, up to the matching close paren
            depth, i = 1, match.end()
            while i < len(text) and depth:
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            call = text[match.start():i]
            if "creationflags" not in call:
                line = text[:match.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert not offenders, (
        "these spawn a visible console window under pythonw.exe — "
        "pass creationflags=quiet_creationflags(): " + ", ".join(offenders)
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")
def test_a_real_child_runs_fine_with_the_flag(tmp_path):
    """The flag must not break the thing it silences."""
    proc = subprocess.run(
        [sys.executable, "-c", "print('quiet')"],
        capture_output=True, text=True, timeout=30,
        creationflags=quiet_creationflags(),
    )
    assert proc.returncode == 0
    assert "quiet" in proc.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")
def test_sandbox_commands_are_silent_and_still_work(tmp_path):
    from dariusai.agent.sandbox import Sandbox
    result = Sandbox(root=tmp_path).run(f'"{sys.executable}" -c "print(42)"')
    assert result.returncode == 0
    assert "42" in result.output
