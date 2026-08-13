"""DariusAI — version identity for the whole project.

One source of truth. `__version__` is the PEP 440 string (what pip,
`importlib.metadata` and pyproject.toml agree on); the human label shown in
the title bar, the About box and `dariusai --version` is *derived* from it by
`version_display()` rather than written out a second time, so a release bump
can't leave the window advertising a version the package no longer is.

Pre-release suffixes map to the words people actually say: 0.55.0a0 reads as
"alpha 0.55".
"""

from __future__ import annotations

import re

__version__ = "0.75.0a0"

_STAGES = {"a": "alpha", "b": "beta", "rc": "rc"}
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:(a|b|rc)(\d+))?$")


def version_display(version: str = __version__) -> str:
    """'0.55.0a0' -> 'alpha 0.55'. The patch number only shows up once it's
    non-zero ('0.55.2a0' -> 'alpha 0.55.2') — an alpha's third digit is noise
    on a title bar until it means something. Anything that doesn't parse is
    returned unchanged rather than raising: a version string is never worth
    crashing the app over."""
    match = _VERSION_RE.match(version)
    if not match:
        return version
    major, minor, patch, stage, _stage_num = match.groups()
    number = f"{major}.{minor}" + (f".{patch}" if patch and patch != "0" else "")
    return f"{_STAGES[stage]} {number}" if stage else number


VERSION_DISPLAY = version_display()

__all__ = ["__version__", "VERSION_DISPLAY", "version_display"]
