"""Finding Blender, and installing DariusAI's add-on into it.

The add-on ships inside DariusAI (`addon/blender/darius_blender_mcp`), so
"install" is a copy into Blender's add-ons directory followed by one
headless run to enable it and save the preference. No clone, no pip, no
zip for the user to find.

Nothing here touches any other add-on: the module name is
`darius_blender_mcp`, which is deliberately distinct from anything else a
user may already have installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .agent.sandbox import quiet_creationflags
from .os_integration import project_root

ADDON_MODULE = "darius_blender_mcp"
ENABLE_TIMEOUT = 180
_VERSION_DIR_RE = re.compile(r"^\d+\.\d+$")


@dataclass
class BlenderInstall:
    executable: Path
    version: str = ""
    config_dir: Path | None = None
    addons_dir: Path | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "executable": str(self.executable),
            "version": self.version,
            "addons_dir": str(self.addons_dir) if self.addons_dir else None,
            "installed": self.addon_installed(),
        }

    def addon_installed(self) -> bool:
        return bool(self.addons_dir and (self.addons_dir / ADDON_MODULE).is_dir())


def addon_source() -> Path:
    """Where the add-on lives inside DariusAI."""
    return project_root() / "addon" / "blender" / ADDON_MODULE


# ---- finding Blender --------------------------------------------------------

def _candidate_executables() -> list[Path]:
    found: list[Path] = []
    on_path = shutil.which("blender")
    if on_path:
        found.append(Path(on_path))

    if sys.platform == "win32":
        roots = [Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")),
                 Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))]
        for root in roots:
            base = root / "Blender Foundation"
            if base.is_dir():
                # "Blender 5.2", "Blender 4.2", … — newest first.
                for folder in sorted(base.glob("Blender*"), reverse=True):
                    exe = folder / "blender.exe"
                    if exe.is_file():
                        found.append(exe)
    elif sys.platform == "darwin":
        for path in (Path("/Applications/Blender.app/Contents/MacOS/Blender"),
                     Path.home() / "Applications/Blender.app/Contents/MacOS/Blender"):
            if path.is_file():
                found.append(path)
    else:
        for path in (Path("/usr/bin/blender"), Path("/usr/local/bin/blender"),
                     Path("/snap/bin/blender")):
            if path.is_file():
                found.append(path)

    seen, unique = set(), []
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _version_from_path(executable: Path) -> str:
    """Read the version off the install folder — "Blender 5.2" — rather
    than launching Blender to ask. Starting Blender to find out where its
    add-ons live is slow enough to be noticeable in the UI."""
    for part in reversed(executable.parts):
        match = re.search(r"(\d+\.\d+)", part)
        if match:
            return match.group(1)
    return ""


def _config_root() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / \
            "Blender Foundation" / "Blender"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Blender"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "blender"


def _addons_dir_for(version: str) -> Path | None:
    root = _config_root()
    if not root.is_dir():
        return None
    if version and (root / version).is_dir():
        return root / version / "scripts" / "addons"
    versions = sorted((d.name for d in root.iterdir()
                       if d.is_dir() and _VERSION_DIR_RE.match(d.name)),
                      key=lambda v: tuple(int(p) for p in v.split(".")), reverse=True)
    if not versions:
        return None
    return root / versions[0] / "scripts" / "addons"


def find_blender() -> BlenderInstall | None:
    """The Blender this machine would run, or None."""
    for executable in _candidate_executables():
        version = _version_from_path(executable)
        install = BlenderInstall(executable=executable, version=version)
        install.config_dir = _config_root()
        install.addons_dir = _addons_dir_for(version)
        if install.addons_dir is None:
            # A fresh Blender that has never been launched has no config
            # directory yet; the add-ons path is still predictable.
            if version:
                install.addons_dir = _config_root() / version / "scripts" / "addons"
                install.notes.append("Blender's config directory does not exist yet")
        return install
    return None


# ---- installing -------------------------------------------------------------

def install_addon(install: BlenderInstall | None = None, enable: bool = True,
                  on_progress=None) -> dict:
    """Copy the add-on in and enable it. Returns a report."""
    def say(message: str) -> None:
        if on_progress:
            on_progress(message)

    install = install or find_blender()
    if install is None:
        return {"ok": False, "error": "Blender was not found on this machine."}

    source = addon_source()
    if not source.is_dir():
        return {"ok": False, "error": f"add-on source missing at {source}"}
    if install.addons_dir is None:
        return {"ok": False, "error": "could not work out Blender's add-ons directory"}

    target = install.addons_dir / ADDON_MODULE
    say(f"installing into {target}")
    try:
        install.addons_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # Replace wholesale: a partial upgrade that leaves a stale
            # module behind is the hardest kind of breakage to diagnose.
            shutil.rmtree(target)
        shutil.copytree(source, target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    except OSError as exc:
        return {"ok": False, "error": f"could not copy the add-on: {exc}"}

    report = {"ok": True, "installed_to": str(target), "enabled": False,
              "executable": str(install.executable), "version": install.version}
    if not enable:
        return report

    say("enabling it in Blender…")
    enabled, detail = enable_addon(install)
    report["enabled"] = enabled
    report["detail"] = detail
    if not enabled:
        report["hint"] = ("Enable it by hand: Blender → Edit → Preferences → Add-ons → "
                          "search 'DariusAI'.")
    say("done" if enabled else "copied, but could not enable it automatically")
    return report


def enable_addon(install: BlenderInstall | None = None) -> tuple[bool, str]:
    """Run Blender headless once to enable the add-on and save prefs."""
    install = install or find_blender()
    if install is None:
        return False, "Blender was not found"

    expression = (
        "import bpy;"
        f"bpy.ops.preferences.addon_enable(module='{ADDON_MODULE}');"
        "bpy.ops.wm.save_userpref();"
        "print('DARIUS_ADDON_ENABLED')"
    )
    try:
        proc = subprocess.run(
            [str(install.executable), "--background", "--factory-startup",
             "--python-expr", expression],
            capture_output=True, text=True, timeout=ENABLE_TIMEOUT,
            creationflags=quiet_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not run Blender: {exc}"

    output = (proc.stdout or "") + (proc.stderr or "")
    if "DARIUS_ADDON_ENABLED" in output:
        return True, "enabled"
    return False, (output.strip().splitlines() or ["Blender reported nothing"])[-1]


def uninstall_addon(install: BlenderInstall | None = None) -> dict:
    install = install or find_blender()
    if install is None or install.addons_dir is None:
        return {"ok": False, "error": "Blender was not found"}
    target = install.addons_dir / ADDON_MODULE
    if not target.exists():
        return {"ok": True, "removed": False}
    try:
        shutil.rmtree(target)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "removed": True, "path": str(target)}


def launch_blender(install: BlenderInstall | None = None) -> dict:
    """Start Blender so the add-on can come up and the light can go green."""
    install = install or find_blender()
    if install is None:
        return {"ok": False, "error": "Blender was not found"}
    try:
        subprocess.Popen(  # noqa: S603 - a known executable the user asked us to start
            [str(install.executable)],
            creationflags=quiet_creationflags(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return {"ok": False, "error": f"could not start Blender: {exc}"}
    return {"ok": True, "executable": str(install.executable)}


def status() -> dict:
    """Everything the UI needs to describe the Blender side."""
    install = find_blender()
    if install is None:
        return {"found": False, "installed": False,
                "detail": "Blender was not found on this machine.",
                "addon_source": str(addon_source())}
    return {
        "found": True,
        **install.as_dict(),
        "addon_source": str(addon_source()),
        "notes": install.notes,
    }
