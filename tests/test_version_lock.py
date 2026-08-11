"""The app source and the version number must move together.

"Bump the version whenever something changes" is a rule that gets forgotten,
and forgetting is silent: the badge, the About box and `--version` all go on
confidently reporting a version that isn't what's running — the exact
confusion the version display was added to end.

version_lock.json records a fingerprint of the app source next to the
version it belongs to. This recomputes it. A mismatch means the app changed
without a bump, and the fix is one command:

    python tools/bump_version.py --patch      (or --minor, or --set 0.57)

Only src/ and launch.pyw are fingerprinted — editing a test or a document
doesn't change what a user runs, so it doesn't demand a new version.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import dariusai
from bump_version import compose, fingerprint, parse, source_files

LOCK = ROOT / "version_lock.json"


def test_lock_file_exists():
    assert LOCK.is_file(), "run: python tools/bump_version.py --relock"


def test_the_locked_version_is_the_running_version():
    locked = json.loads(LOCK.read_text(encoding="utf-8"))
    assert locked["version"] == dariusai.__version__, (
        f"version_lock.json says {locked['version']} but the package is "
        f"{dariusai.__version__} — run: python tools/bump_version.py --relock"
    )


def test_source_has_not_changed_without_a_version_bump():
    locked = json.loads(LOCK.read_text(encoding="utf-8"))
    current = fingerprint()
    assert current == locked["fingerprint"], (
        f"{len(source_files())} app source files no longer match the fingerprint locked for "
        f"{locked['version']}. Something changed without a version bump.\n"
        f"Fix: python tools/bump_version.py --patch   (or --minor / --set X.Y)"
    )


def test_the_lock_belongs_to_the_current_version():
    """Was `assert __version__ == "0.56.0a0"` — a frozen literal that failed on
    every bump. The real invariant is that the lock file records the version
    actually running, so a relock can never quietly attach to an older one."""
    locked = json.loads(LOCK.read_text(encoding="utf-8"))
    assert locked["version"] == dariusai.__version__
    assert locked["files"] == len(source_files())


# ---- the bump tool itself --------------------------------------------------

@pytest.mark.parametrize("current,bump,expected", [
    ("0.56.0a0", "minor", "0.57.0a0"),
    ("0.56.0a0", "patch", "0.56.1a0"),
    ("0.56.3a0", "minor", "0.57.0a0"),   # a minor bump resets the patch
])
def test_bump_arithmetic(current, bump, expected):
    major, minor, patch, stage, num = parse(current)
    if bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    assert compose(major, minor, patch, stage, num) == expected


def test_fingerprint_is_stable_across_runs():
    assert fingerprint() == fingerprint()


def test_fingerprint_reacts_to_a_source_change(tmp_path, monkeypatch):
    """A guard that never fires guards nothing."""
    import bump_version
    before = bump_version.fingerprint()

    extra = ROOT / "src" / "dariusai" / "_fingerprint_probe.py"
    extra.write_text("# temporary\n", encoding="utf-8")
    try:
        assert bump_version.fingerprint() != before
    finally:
        extra.unlink()
    assert bump_version.fingerprint() == before


def test_fingerprint_ignores_line_ending_churn(monkeypatch):
    """A checkout that flips CRLF is not a code change."""
    import bump_version
    target = ROOT / "src" / "dariusai" / "__init__.py"
    original = target.read_bytes()
    before = bump_version.fingerprint()
    try:
        target.write_bytes(original.replace(b"\n", b"\r\n"))
        assert bump_version.fingerprint() == before
    finally:
        target.write_bytes(original)


def test_the_tool_runs_and_reports(tmp_path):
    """--relock must be a safe no-op that leaves the version alone."""
    before = dariusai.__version__
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bump_version.py"), "--relock"],
        capture_output=True, text=True, timeout=60, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert before in proc.stdout
    assert json.loads(LOCK.read_text(encoding="utf-8"))["version"] == before
