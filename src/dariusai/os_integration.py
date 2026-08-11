"""Windows-specific OS integration: the "start with Windows" toggle and the
Desktop / Start Menu shortcuts.

Adds/removes a value under HKCU\\...\\Run so DariusAI launches at login,
pointed at the same DariusAI.vbs a user would double-click by hand — same
launch path either way, so there isn't a second thing that can drift out of
sync with the real app.

The shortcuts exist for the icon. Anything launched as "pythonw.exe some
script" wears Python's icon everywhere Windows shows the *app* rather than
the window — on the desktop, in the Start Menu, in search, and on the
taskbar once it's pinned — because as far as Explorer is concerned the app
*is* pythonw.exe. A .lnk with an explicit IconLocation is what replaces
that with the brain, and it's also what gets pinned when you pin the app,
so the pinned tile keeps the brain too.

This module never runs on its own initiative. Modifying a registry-level
startup entry, or writing a file to the desktop, is persistent, system-level
configuration — it only executes when a user explicitly flips the "Start
with Windows" checkbox or asks for shortcuts in Settings. key_path/
value_name are parameters (not just module constants) specifically so tests
can point this at a disposable scratch key instead of the real Run key, and
every shortcut path is likewise injectable.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import winreg
from pathlib import Path

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEFAULT_VALUE_NAME = "DariusAI"
SHORTCUT_NAME = "DariusAI.lnk"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]  # src/dariusai/os_integration.py -> project root


def _launch_command() -> str:
    vbs = project_root() / "DariusAI.vbs"
    return f'wscript.exe "{vbs}"'


def is_enabled(value_name: str = DEFAULT_VALUE_NAME, key_path: str = RUN_KEY_PATH) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, value_name)
            return True
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool, value_name: str = DEFAULT_VALUE_NAME, key_path: str = RUN_KEY_PATH) -> bool:
    """Returns the resulting enabled state (read back, not just assumed)."""
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
        if enabled:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, value_name)
            except FileNotFoundError:
                pass
    return is_enabled(value_name, key_path)


# --------------------------------------------------------------------------
# Desktop / Start Menu shortcuts
# --------------------------------------------------------------------------

def icon_path() -> Path:
    from .viz.server import STATIC_DIR
    return STATIC_DIR / "brain.ico"


APP_USER_MODEL_ID = "DariusAI.Harness"


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """Give the process its own taskbar identity.

    Without this, Windows identifies the app by the executable hosting it —
    pythonw.exe — and the taskbar button shows **Python's** icon no matter
    what icon the window itself carries. Setting an explicit AppUserModelID
    makes the taskbar treat DariusAI as its own application and use the
    window's icon. It must happen before the first window is created; once
    a window exists, the identity is already baked in.

    Returns whether it took, so a caller can log it rather than guess.
    """
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:
        return False  # never worth failing a launch over


def apply_window_icon(title: str = "DariusAI", ico: Path | None = None, attempts: int = 40) -> bool:
    """Belt-and-braces: push the .ico onto the window with WM_SETICON.

    pywebview already sets Form.Icon, but that's one code path in one
    backend and it's silently skipped if the file can't be read — and a
    silently-skipped icon is exactly the bug being chased here. Loading the
    .ico directly at both the small (title bar / Alt-Tab) and large
    (taskbar / Alt-Tab preview) sizes leaves nothing to chance.

    Polls for the window because it's called from a ready-hook that can fire
    fractionally before the native window exists.
    """
    import ctypes
    import time as _time

    ico = Path(ico) if ico else icon_path()
    if not ico.exists():
        return False

    user32 = ctypes.windll.user32
    IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
    WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
    small_cx, small_cy = user32.GetSystemMetrics(49), user32.GetSystemMetrics(50)  # SM_CXSMICON/CYSMICON
    big_cx, big_cy = user32.GetSystemMetrics(11), user32.GetSystemMetrics(12)     # SM_CXICON/CYICON

    for _ in range(attempts):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            small = user32.LoadImageW(None, str(ico), IMAGE_ICON, small_cx, small_cy, LR_LOADFROMFILE)
            big = user32.LoadImageW(None, str(ico), IMAGE_ICON, big_cx, big_cy, LR_LOADFROMFILE)
            if not small and not big:
                big = user32.LoadImageW(None, str(ico), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if small:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
            if big:
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
            return bool(small or big)
        _time.sleep(0.05)
    return False


def _pythonw() -> Path:
    """The project venv's pythonw.exe — the same interpreter DariusAI.vbs
    picks, and deliberately *not* whatever python is on PATH, which may not
    have this project's dependencies."""
    return project_root() / ".venv" / "Scripts" / "pythonw.exe"


def create_shortcut(destination: Path | str, target: Path | str | None = None,
                    arguments: str | None = None, icon: Path | str | None = None) -> Path:
    """Write a .lnk at `destination`, via WScript.Shell driven through
    cscript. COM is the only supported way to author a shortcut, and going
    through cscript gets it without adding pywin32 to a project that
    otherwise needs no COM at all.

    The shortcut points straight at pythonw.exe rather than at
    DariusAI.vbs: a shortcut can only lend its icon and its taskbar identity
    to the process it actually starts, and going via wscript.exe would start
    the app one process removed from the shortcut — so the running window
    would no longer be associated with it, and pinning it from the taskbar
    would pin Python again. pythonw.exe shows no console either way, which
    is the only reason the .vbs wrapper exists.
    """
    destination = Path(destination)
    target = Path(target) if target else _pythonw()
    icon = Path(icon) if icon else icon_path()
    if arguments is None:
        arguments = f'"{project_root() / "launch.pyw"}"'

    destination.parent.mkdir(parents=True, exist_ok=True)
    script = "\n".join([
        'Set shell = CreateObject("WScript.Shell")',
        f'Set sc = shell.CreateShortcut({_vbs_str(str(destination))})',
        f'sc.TargetPath = {_vbs_str(str(target))}',
        f'sc.Arguments = {_vbs_str(arguments)}',
        f'sc.WorkingDirectory = {_vbs_str(str(project_root()))}',
        f'sc.IconLocation = {_vbs_str(f"{icon},0")}',
        'sc.Description = "DariusAI — self-learning coding agent"',
        'sc.Save',
    ])

    handle, script_path = tempfile.mkstemp(suffix=".vbs", text=True)
    try:
        with os.fdopen(handle, "w", encoding="mbcs") as fh:
            fh.write(script)
        from .agent.sandbox import quiet_creationflags
        result = subprocess.run(
            ["cscript.exe", "//nologo", "//B", script_path],
            capture_output=True, text=True,
            creationflags=quiet_creationflags(),  # no console flash on the desktop
        )
        if result.returncode != 0 or not destination.exists():
            raise OSError(f"could not create {destination}: {result.stderr.strip() or result.stdout.strip()}")
    finally:
        os.unlink(script_path)
    return destination


def _vbs_str(value: str) -> str:
    """VBScript string literal — doubled quotes are its only escape."""
    return '"' + value.replace('"', '""') + '"'


def desktop_dir() -> Path:
    return Path(os.path.expanduser("~")) / "Desktop"


def start_menu_dir() -> Path:
    appdata = os.environ.get("APPDATA") or (Path(os.path.expanduser("~")) / "AppData/Roaming")
    return Path(appdata) / "Microsoft/Windows/Start Menu/Programs"


def install_shortcuts(desktop: bool = True, start_menu: bool = True,
                      desktop_root: Path | None = None, start_menu_root: Path | None = None) -> list[str]:
    """Create the brain-icon shortcuts the user asked for. Returns the paths
    written, so the caller can report exactly what landed where."""
    written = []
    if desktop:
        written.append(str(create_shortcut((desktop_root or desktop_dir()) / SHORTCUT_NAME)))
    if start_menu:
        written.append(str(create_shortcut((start_menu_root or start_menu_dir()) / SHORTCUT_NAME)))
    return written


def delete_key_for_test(key_path: str) -> None:
    """Test-only cleanup — removes an entire scratch subkey tree so tests
    don't leave residue in the registry between runs."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS) as key:
            pass
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except FileNotFoundError:
        pass
