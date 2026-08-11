"""Bump the version and re-lock the source fingerprint.

  python tools/bump_version.py --set 0.56      -> alpha 0.56
  python tools/bump_version.py --minor         -> 0.56 -> 0.57
  python tools/bump_version.py --patch         -> 0.56 -> 0.56.1
  python tools/bump_version.py --relock        -> keep the version, accept the source as-is

Why a lock file exists at all: "remember to bump the version whenever you
change something" is a rule that gets forgotten, and forgetting is silent —
the app then reports a version that doesn't match what's running, which is
exactly the confusion the version badge was added to end.

So `version_lock.json` records a fingerprint of the app source alongside the
version it belongs to. tests/test_version_lock.py recomputes it and fails
when the source has moved on without a bump. The rule enforces itself
instead of relying on memory.

Only the *app* is fingerprinted — src/ and launch.pyw. Editing a test or a
doc doesn't change what a user runs, so it doesn't demand a new version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "src" / "dariusai" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"
LOCK = ROOT / "version_lock.json"

# What counts as "the app changed".
SOURCE_GLOBS = ("src/**/*.py", "src/**/static/index.html", "launch.pyw")
EXCLUDE_PARTS = ("__pycache__", ".venv", "vendor", ".pytest_cache")


def source_files() -> list[Path]:
    seen: set[Path] = set()
    for pattern in SOURCE_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file() and not any(part in EXCLUDE_PARTS for part in path.parts):
                seen.add(path)
    return sorted(seen)


def fingerprint() -> str:
    """Hash of every app source file, path included so a rename counts as a
    change. Line endings are normalised — a checkout that flips CRLF is not
    a code change and shouldn't demand a version bump."""
    digest = hashlib.sha256()
    for path in source_files():
        digest.update(str(path.relative_to(ROOT)).replace("\\", "/").encode("utf-8"))
        # Strip every CR rather than only CRLF pairs: a file that already
        # had CRLF would otherwise turn into CR-CR-LF and hash differently,
        # so the guard would fire on a checkout rather than on a real edit.
        digest.update(path.read_bytes().replace(b"\r", b""))
    return digest.hexdigest()


def read_version() -> str:
    match = re.search(r'^__version__ = "([^"]+)"', INIT.read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit("could not find __version__ in " + str(INIT))
    return match.group(1)


def parse(version: str) -> tuple[int, int, int, str, int]:
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?$", version)
    if not match:
        raise SystemExit(f"cannot parse version {version!r}")
    major, minor, patch, stage, num = match.groups()
    return int(major), int(minor), int(patch or 0), stage or "", int(num or 0)


def compose(major: int, minor: int, patch: int, stage: str, num: int) -> str:
    return f"{major}.{minor}.{patch}" + (f"{stage}{num}" if stage else "")


def write_version(new: str) -> None:
    init = INIT.read_text(encoding="utf-8")
    INIT.write_text(re.sub(r'^__version__ = "[^"]+"', f'__version__ = "{new}"', init, count=1, flags=re.M),
                    encoding="utf-8")
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    PYPROJECT.write_text(re.sub(r'^version = "[^"]+"', f'version = "{new}"', pyproject, count=1, flags=re.M),
                         encoding="utf-8")


def write_lock(version: str) -> str:
    digest = fingerprint()
    LOCK.write_text(json.dumps({
        "version": version,
        "fingerprint": digest,
        "files": len(source_files()),
        "_comment": "Regenerate with tools/bump_version.py. A mismatch means the app "
                    "changed without a version bump; tests/test_version_lock.py enforces it.",
    }, indent=2) + "\n", encoding="utf-8")
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--set", dest="explicit", help="version to set, e.g. 0.56 or 0.56.0a0")
    group.add_argument("--minor", action="store_true")
    group.add_argument("--patch", action="store_true")
    group.add_argument("--relock", action="store_true", help="keep the version, re-record the fingerprint")
    parser.add_argument("--stage", default=None, help="a|b|rc|final (default: keep current)")
    args = parser.parse_args()

    current = read_version()
    major, minor, patch, stage, num = parse(current)

    if args.relock:
        new = current
    else:
        if args.explicit:
            bits = parse(args.explicit if re.search(r"[abrc]", args.explicit) else args.explicit + f"{stage or 'a'}{num}")
            major, minor, patch = bits[0], bits[1], bits[2]
            stage, num = (bits[3], bits[4]) if bits[3] else (stage, num)
        elif args.minor:
            minor, patch = minor + 1, 0
        elif args.patch:
            patch += 1
        if args.stage is not None:
            stage, num = ("", 0) if args.stage == "final" else (args.stage, 0)
        new = compose(major, minor, patch, stage, num)
        write_version(new)

    digest = write_lock(new)
    sys.path.insert(0, str(ROOT / "src"))
    from dariusai import version_display  # imported after writing, to echo the real thing
    print(f"version: {current} -> {new}  ({version_display(new)})")
    print(f"locked {len(source_files())} source files @ {digest[:16]}…")


if __name__ == "__main__":
    main()
