"""Floating, movable OS window for the neural-network viz — the project
spec calls for "a window always open that we can move around", which a
browser tab can't give you (browser chrome, one-of-many-tabs, easily lost
behind other windows). pywebview opens a real native window backed by the
system webview (WebView2 on Windows), with a normal draggable title bar,
resizable and independently movable from everything else — while the
content is exactly the same self-contained HTML/JS page the FastAPI server
already serves, so there's exactly one implementation of the graph view,
not a duplicated native one.

The window is frameless (no native title bar) — the app draws its own
title bar and menu bar in HTML, matching a real desktop app rather than a
browser tab pointed at a page. Dragging that bar moves the OS window
because it carries pywebview's `pywebview-drag-region` class, which
pywebview implements itself in injected JS. It deliberately does *not*
rely on `-webkit-app-region: drag`: WebView2 ignores that CSS unless Edge
is launched with the msWebView2EnableDraggableRegions feature flag, so on
Windows the window was pinned wherever it first opened. The close button
calls back into Python through pywebview's js_api bridge instead of JS
being able to close a native window directly.

The server runs in a background thread so the window and the rest of the
process (agent loop, CLI) can run concurrently. Startup is synchronized on
uvicorn.Server.started — pywebview used to be handed the URL immediately
after starting the server thread, with no guarantee the socket was actually
bound and accepting connections yet; on a slower machine (or one already
running something on the default port) the window would load before the
server was ready, and the page's own JS would see the initial /api/graph
fetch fail ("Failed to load graph: Failed to fetch") even though the server
came up half a second later. Waiting on `.started` — and retrying on the
next port if the one requested is already taken — removes both failure
modes instead of papering over them with a fixed sleep.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import uvicorn

from .server import STATIC_DIR, create_app

STARTUP_TIMEOUT = 10.0
PORT_ATTEMPTS = 20
# Passed to webview.start(icon=...). Not cosmetic: pywebview's winforms
# backend falls back to extracting the icon out of sys.executable when it
# isn't given one, which under pythonw.exe is Python's own icon — that's
# what the window and its taskbar button would otherwise wear. Regenerate
# it from brain.png with tools/make_icons.py.
ICON_PATH = STATIC_DIR / "brain.ico"


def _start_server(app, host: str, port: int) -> tuple[uvicorn.Server, int]:
    for candidate in range(port, port + PORT_ATTEMPTS):
        config = uvicorn.Config(app, host=host, port=candidate, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        deadline = time.time() + STARTUP_TIMEOUT
        while time.time() < deadline:
            if server.started:
                return server, candidate
            if not thread.is_alive():
                break  # startup failed (almost always: port already in use) — try the next one
            time.sleep(0.02)
    raise OSError(f"could not bind to any port in [{port}, {port + PORT_ATTEMPTS})")


EXIT_GRACE_SECONDS = 2.0


def _force_exit() -> None:
    """Last resort. Nothing in this app owns state that a clean interpreter
    shutdown would flush — the brain store writes synchronously and the
    editor saves on command — so an exit that always happens beats an exit
    that's tidy but can be held open by a stuck WebView2 message pump or a
    thread that never noticed it was supposed to stop."""
    os._exit(0)


class DesktopAPI:
    """Exposed to the page as `window.pywebview.api.*` — the bridge for
    things only the OS-level window object can do. A frameless window (no
    native title bar) gets none of the OS's own minimize/maximize/close
    affordances — no double-click-to-maximize, no Aero Snap buttons — so
    the page's own custom title bar has to drive all of it through here."""

    def __init__(self, force_exit=_force_exit, exit_grace: float = EXIT_GRACE_SECONDS) -> None:
        self._window = None  # set after create_window(), which needs this object first
        self._maximized = False
        self._icon = None  # set by tray.start_tray_icon() once the tray icon is up, if it is
        self._quitting = False
        # Injectable so the test suite can exercise the real shutdown path
        # without os._exit(0) taking the pytest process down with it.
        self._force_exit = force_exit
        self._exit_grace = exit_grace

    def quit(self) -> None:
        """The one and only shutdown path. Every exit route — the ✕ button,
        File -> Exit, the tray's Exit, Alt-F4, and webview.start() returning
        on its own — funnels through here, because when they each did their
        own thing the app leaked a live process on every close and you'd
        find a stack of them in Task Manager days later.

        Idempotent (the routes overlap: ✕ destroys the window, which then
        fires the `closed` event, which lands here again), and backed by a
        hard exit so that "closed" always means the process is gone."""
        if self._quitting:
            return
        self._quitting = True

        icon, window, self._icon = self._icon, self._window, None
        if icon is not None:
            try:
                icon.stop()  # removes the notification-area icon and ends pystray's loop
            except Exception:
                pass
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass  # already gone (OS-initiated close) — the point is just that it is

        timer = threading.Timer(self._exit_grace, self._force_exit)
        timer.daemon = True  # must never be the thread that keeps us alive
        timer.start()
        self._exit_timer = timer

    def close(self) -> None:
        """The ✕ button quits — it does not hide. Hide-to-tray lives on
        minimize, so "keep the app open in the tray" is still one click
        away, but the button shaped like a close button closes."""
        self.quit()

    def minimize(self) -> None:
        """The hide-to-tray affordance, when a tray icon is actually up to
        get the window back from."""
        if not self._window:
            return
        if self._icon:
            self._window.hide()
        else:
            self._window.minimize()

    def toggle_maximize(self) -> bool:
        """Returns the new maximized state so the title bar button can
        swap its icon (⬜ to maximize / ❐ to restore)."""
        if not self._window:
            return self._maximized
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()
        self._maximized = not self._maximized
        return self._maximized

    def pick_folder(self) -> str | None:
        if not self._window:
            return None
        result = self._window.create_file_dialog(self._folder_dialog_type())
        return result[0] if result else None

    @staticmethod
    def _folder_dialog_type():
        import webview
        return webview.FOLDER_DIALOG


def launch(
    home: Path | str,
    host: str = "127.0.0.1",
    port: int = 8765,
    blocking: bool = True,
    project_dir: Path | str | None = None,
) -> None:
    """Start the viz server, wait until it's actually accepting connections,
    then open the floating window pointed at whatever port it actually
    bound (which may not be `port`, if that one was taken)."""
    import webview  # imported lazily — only needed when actually opening a window

    from ..os_integration import set_app_user_model_id
    set_app_user_model_id()  # must precede the first window, or the taskbar keeps Python's icon

    app = create_app(home, project_dir=project_dir)
    _server, bound_port = _start_server(app, host, port)

    api = DesktopAPI()
    window = webview.create_window(
        "DariusAI",
        f"http://{host}:{bound_port}/",
        width=1280,
        height=840,
        resizable=True,
        min_size=(700, 450),
        frameless=True,
        # easy_drag would move the window from a press anywhere on the page,
        # which would fight the canvas rotate/pan and the floating panels —
        # dragging is limited to the page's own titlebar, which opts in with
        # the .pywebview-drag-region class.
        easy_drag=False,
        js_api=api,
    )
    api._window = window
    attach_shutdown(window, api)

    def on_ready():
        from ..os_integration import apply_window_icon
        from .tray import start_tray_icon
        apply_window_icon()
        start_tray_icon(window, api)

    if blocking:
        webview.start(on_ready, icon=str(ICON_PATH) if ICON_PATH.exists() else None)
        api.quit()  # start() returned — the window is gone, so nothing else may outlive it


def attach_shutdown(window, api: DesktopAPI) -> None:
    """Route the OS's own ways of closing the window into the same quit.

    Alt-F4, the taskbar's right-click Close and Task Manager's "End task"
    never touch the page's ✕ button, so without this the window would
    vanish while the tray icon, the HTTP server and the process itself
    carried on — invisible, unkillable except through Task Manager, and
    stacking up one per launch."""
    window.events.closed += lambda: api.quit()
