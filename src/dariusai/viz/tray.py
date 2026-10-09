"""System tray icon (Windows notification area, near the clock).

The icon is a pink brain with a yellow D (tools/make_tray_icon.py). Its menu:

  — Darius AI —            show the window (also the click / default action)
  Stop                     stop the agent's running turn
  Reload                   reload the window
  Open project…            the native folder picker, same as File → Open
  Permission requests (N)  only while some are waiting: Allow / Deny each
  Settings
  Exit

A permission request also raises a Windows notification from the tray, so a
request made while the window is minimised doesn't sit unseen for the five
minutes before it times out.

Minimising hides the window to the tray. Closing does not: ✕, File → Exit
and the tray's Exit all end the process outright (see DesktopAPI.quit),
because a close button that leaves a live process behind means one more of
them every time the app is opened.

The tray talks to the app over its own local HTTP API (base_url) rather
than reaching into the server's objects: the same calls the page makes, so
there is one code path for stop / permissions / project, and the tray works
the same whichever launcher built the window.

Built best-effort: if pystray/Pillow aren't available or icon creation
fails for any reason, the app still runs — window controls just fall back
to plain minimize/close instead of hide-to-tray (see DesktopAPI).
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import PureWindowsPath

POLL_SECONDS = 1.5


def _http(base_url: str, method: str, path: str, body: dict | None = None, timeout: float = 4.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def _short(path: str, n: int = 48) -> str:
    """The tail of a path, which is the part that says what it is."""
    p = PureWindowsPath(path)
    tail = str(PureWindowsPath(*p.parts[-2:])) if len(p.parts) > 2 else str(p)
    return tail if len(tail) <= n else "…" + tail[-(n - 1):]


class TrayController:
    def __init__(self, window, api, base_url: str):
        self.window, self.api, self.base_url = window, api, base_url.rstrip("/")
        self.requests: list[dict] = []
        self.icon = None
        self._stop = threading.Event()

    # ---- actions ------------------------------------------------------
    def show(self, *_):
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            pass

    def stop_turn(self, *_):
        try:
            res = _http(self.base_url, "POST", "/api/chat/stop", {})
            if self.icon and res and res.get("stopped"):
                self.icon.notify("Darius will stop at his next step.", "DariusAI")
        except Exception:
            pass

    def reload(self, *_):
        self.show()
        try:
            self.window.evaluate_js("location.reload()")
        except Exception:
            pass

    def open_project(self, *_):
        self.show()   # the folder picker belongs to the window
        try:
            self.window.evaluate_js("window.__menuAction && window.__menuAction('open-folder')")
        except Exception:
            pass

    def settings(self, *_):
        self.show()
        try:
            self.window.evaluate_js("window.__menuAction && window.__menuAction('show-settings')")
        except Exception:
            pass

    def answer(self, req_id: str, allow: bool):
        try:
            _http(self.base_url, "POST", f"/api/permissions/{req_id}", {"allow": allow})
        except Exception:
            pass   # already answered in the window, or expired
        self.poll_once()

    def exit(self, *_):
        self.api.quit()   # same shutdown as ✕ and File -> Exit — one path, no leaked processes

    # ---- menu ---------------------------------------------------------
    def _request_items(self):
        import pystray
        items = []
        for r in self.requests:
            rid = r["id"]
            items.append(pystray.MenuItem(
                "Read " + _short(r.get("path", "")),
                pystray.Menu(
                    pystray.MenuItem("Allow (this turn, read-only)", lambda *_ , rid=rid: self.answer(rid, True)),
                    pystray.MenuItem("Deny", lambda *_, rid=rid: self.answer(rid, False)),
                ),
            ))
        return items

    def build_menu(self):
        import pystray
        return pystray.Menu(
            pystray.MenuItem("— Darius AI —", self.show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop", self.stop_turn),
            pystray.MenuItem("Reload", self.reload),
            pystray.MenuItem("Open project…", self.open_project),
            pystray.MenuItem(
                lambda item: f"Permission requests ({len(self.requests)})",
                pystray.Menu(lambda: self._request_items()),
                visible=lambda item: bool(self.requests),     # only while something is waiting
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings", self.settings),
            pystray.MenuItem("Exit", self.exit),
        )

    # ---- permission watcher -------------------------------------------
    def poll_once(self):
        try:
            current = _http(self.base_url, "GET", "/api/permissions") or []
        except Exception:
            return
        before = {r["id"] for r in self.requests}
        self.requests = current
        new = [r for r in current if r["id"] not in before]
        if self.icon is None:
            return
        if new:
            r = new[-1]
            more = f" (+{len(current) - 1} more)" if len(current) > 1 else ""
            try:
                self.icon.notify(f"Darius wants to read {_short(r.get('path', ''), 60)}{more}. "
                                 "Right-click the tray icon → Permission requests.",
                                 "Permission request")
            except Exception:
                pass
        if new or len(current) != len(before):
            self.icon.title = (f"DariusAI — {len(current)} permission request{'s' if len(current) != 1 else ''} waiting"
                               if current else "DariusAI")
            try:
                self.icon.update_menu()
            except Exception:
                pass

    def watch(self):
        while not self._stop.wait(POLL_SECONDS):
            self.poll_once()


def build_tray_icon(window, api, base_url: str = ""):
    import pystray
    from PIL import Image

    from .server import STATIC_DIR

    image_path = STATIC_DIR / "tray.png"
    image = Image.open(image_path if image_path.exists() else STATIC_DIR / "favicon.png")
    ctl = TrayController(window, api, base_url)
    icon = pystray.Icon("DariusAI", image, "DariusAI", ctl.build_menu())
    ctl.icon = icon
    api._icon = icon
    api._tray = ctl
    return icon, ctl


def start_tray_icon(window, api, base_url: str = "") -> None:
    """Best-effort — a tray failure must never take the whole app down
    with it. Runs pystray's own loop in a background thread since
    webview.start() already owns the main thread."""
    try:
        if not base_url:
            try:
                base_url = window.get_current_url() or ""
                base_url = "/".join(base_url.split("/")[:3])
            except Exception:
                base_url = ""
        icon, ctl = build_tray_icon(window, api, base_url)
        threading.Thread(target=icon.run, daemon=True).start()
        if base_url:
            threading.Thread(target=ctl.watch, daemon=True).start()
    except Exception:
        api._icon = None
