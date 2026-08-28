"""Double-click entry point — starts the DariusAI harness with no visible
console window (run via pythonw.exe, either directly or through
DariusAI.vbs), showing a splash screen (logo2.png + a terminal-style boot
log + a progress bar) while the brain index loads and the server binds,
then opens the real floating window.

Run manually with: .venv\\Scripts\\pythonw.exe launch.pyw
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path

# Under pythonw.exe (no console attached) sys.stdout/stderr are None, not
# just silent — anything that logs (uvicorn's default handlers do) crashes
# with "AttributeError: 'NoneType' object has no attribute 'write'" the
# instant it tries. Give them a real no-op sink instead.
if sys.stdout is None or sys.stderr is None:
    import io
    sys.stdout = sys.stderr = io.StringIO()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

LOGO_PATH = ROOT / "logo2.png"
ICON_PATH = ROOT / "src" / "dariusai" / "viz" / "static" / "brain.ico"
DEFAULT_HOME = Path.home() / ".dariusai"
# One place for everything: the editor opens the workbench, new projects
# are created inside it, and the agent works there too.
DEFAULT_PROJECT_DIR = Path("C:/DariusAIWorkbench")

TERM_BG = "#05060f"
TERM_FG = "#6ee7b7"
TERM_DIM = "#5b6289"
TERM_ERR = "#f87171"
ACCENT = "#3b5bdb"
PANEL_BG = "#0d0f20"
BORDER = "#232640"


class ProgressEvent:
    def __init__(self, text, pct, kind="log"):
        self.text = text
        self.pct = pct
        self.kind = kind  # "log" | "error" | "done"


def _warm_runtimes() -> None:
    """Populate the runtime-detection cache in the background. Failures are
    irrelevant here — the page falls back to probing on demand."""
    try:
        from dariusai.agent.runtimes import detect
        detect()
    except Exception:
        pass


def run_backend(events: "queue.Queue[ProgressEvent]") -> None:
    try:
        from dariusai import VERSION_DISPLAY
        events.put(ProgressEvent(f"initializing dariusai harness — {VERSION_DISPLAY}…", 4))

        events.put(ProgressEvent("loading brain index…", 12))
        from dariusai.brain.store import BrainStore
        store = BrainStore(DEFAULT_HOME)
        node_count = len(store.to_graph_payload()["nodes"]) - 1  # minus the coordinator itself
        events.put(ProgressEvent(f"brain index ready — {node_count} node(s) indexed", 30))

        events.put(ProgressEvent("mounting viz server…", 42))
        DEFAULT_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
        from dariusai.viz.server import create_app

        # Pass the default *only* when nothing is saved. create_app treats an
        # explicit project_dir as an override, so passing it unconditionally
        # clobbered the folder chosen via File -> Open Folder on every single
        # restart — the setting was written, then overwritten before anyone
        # could read it.
        saved = store.get_setting("project_dir", "") or ""
        first_run = not (saved and Path(saved).expanduser().is_dir())
        app = create_app(DEFAULT_HOME, project_dir=DEFAULT_PROJECT_DIR if first_run else None)

        events.put(ProgressEvent("starting server…", 55))
        from dariusai.viz.window import _start_server
        # 8780, not 8765: 8765 is reserved for the Blender MCP add-on, which
        # cannot pick another port the way this can (Blender's clients look
        # for it there). _start_server scans upward from here anyway.
        server, port = _start_server(app, "127.0.0.1", 8780)
        events.put(ProgressEvent(f"server listening on 127.0.0.1:{port}", 80))

        # Probe the language runtimes off to one side. Eleven subprocesses
        # run sequentially, and doing it lazily meant they fired on the
        # first request from the page — i.e. during startup, in the
        # foreground. Warming here keeps that off the critical path and out
        # of the way of the first chat message.
        threading.Thread(target=_warm_runtimes, daemon=True).start()

        events.put(ProgressEvent("launching window…", 92))
        events.put(ProgressEvent(f"__READY__{port}", 100, kind="_port"))
        events.put(ProgressEvent("ready.", 100, kind="done"))
    except Exception as exc:  # surfaced in the splash — pythonw has no console to print a traceback to
        events.put(ProgressEvent(f"FATAL: {exc}", 100, kind="error"))


class Splash:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.bound_port = None

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=TERM_BG)
        # Alt-Tab and any window list Windows decides to show the splash in
        # would otherwise use Tk's default feather icon.
        try:
            root.iconbitmap(default=str(ICON_PATH))
        except tk.TclError:
            pass  # a missing/unreadable .ico is not worth failing a launch over

        self.logo_img = tk.PhotoImage(file=str(LOGO_PATH))
        # Downscale the (1254x1254) source logo to a splash-sized square.
        factor = max(1, self.logo_img.width() // 300)
        if factor > 1:
            self.logo_img = self.logo_img.subsample(factor, factor)
        logo_w = self.logo_img.width()

        width = max(360, logo_w + 40)
        term_h = 120
        bar_h = 8
        height = logo_w + term_h + bar_h + 60

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = (sw - width) // 2, (sh - height) // 2
        root.geometry(f"{width}x{height}+{x}+{y}")

        outer = tk.Frame(root, bg=TERM_BG, highlightbackground=BORDER, highlightthickness=1)
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        tk.Label(outer, image=self.logo_img, bg=TERM_BG, bd=0).pack(pady=(16, 10))

        term_frame = tk.Frame(outer, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
        term_frame.pack(padx=18, fill="x")
        self.term = tk.Text(
            term_frame, height=6, bg=PANEL_BG, fg=TERM_FG, insertbackground=TERM_FG,
            font=("Consolas", 9), bd=0, highlightthickness=0, wrap="none", state="disabled",
        )
        self.term.pack(fill="both", expand=True, padx=8, pady=6)
        self.term.tag_configure("dim", foreground=TERM_DIM)
        self.term.tag_configure("err", foreground=TERM_ERR)

        bar_frame = tk.Frame(outer, bg=TERM_BG)
        bar_frame.pack(padx=18, pady=(10, 16), fill="x")
        self.bar_canvas = tk.Canvas(bar_frame, height=bar_h, bg=PANEL_BG, highlightthickness=0)
        self.bar_canvas.pack(fill="x")
        self.bar_fill = self.bar_canvas.create_rectangle(0, 0, 0, bar_h, fill=ACCENT, width=0)
        self._bar_width = width - 36

        self.events: "queue.Queue[ProgressEvent]" = queue.Queue()
        threading.Thread(target=run_backend, args=(self.events,), daemon=True).start()
        root.after(60, self._poll)

    def _log(self, text: str, tag: str | None = None) -> None:
        self.term.configure(state="normal")
        self.term.insert("end", "> " + text + "\n", (tag,) if tag else ())
        self.term.see("end")
        self.term.configure(state="disabled")

    def _set_progress(self, pct: int) -> None:
        w = int(self._bar_width * max(0, min(100, pct)) / 100)
        self.bar_canvas.coords(self.bar_fill, 0, 0, w, 8)

    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                if ev.kind == "_port":
                    self.bound_port = int(ev.text.replace("__READY__", ""))
                    continue
                self._set_progress(ev.pct)
                if ev.kind == "error":
                    self._log(ev.text, tag="err")
                    self._log("close this window to exit.", tag="dim")
                    self.root.bind("<Button-1>", lambda e: self.root.destroy())
                    return  # stop polling — leave the error on screen
                else:
                    self._log(ev.text, tag="dim" if ev.kind == "log" else None)
                if ev.kind == "done":
                    self.root.after(350, self.root.destroy)
                    return
        except queue.Empty:
            pass
        self.root.after(60, self._poll)


def main() -> None:
    # Before any window exists — this is what stops the taskbar showing
    # pythonw.exe's Python icon instead of the app's own.
    from dariusai.os_integration import set_app_user_model_id
    set_app_user_model_id()

    root = tk.Tk()
    splash = Splash(root)
    root.mainloop()

    if splash.bound_port is None:
        return  # backend failed to start — error already shown, nothing to launch

    import webview
    from dariusai.viz.window import DesktopAPI, attach_shutdown

    api = DesktopAPI()
    window = webview.create_window(
        "DariusAI",
        f"http://127.0.0.1:{splash.bound_port}/",
        width=1280, height=840, resizable=True, min_size=(700, 450),
        frameless=True, easy_drag=False, js_api=api,
    )
    api._window = window
    # This launcher builds its own window rather than calling viz.window
    # .launch(), so it has to opt into the same shutdown wiring — it is the
    # path every desktop user actually takes (the .vbs, the shortcut, the
    # Startup entry all land here), so a missing hook here is a leaked
    # process on every single close.
    attach_shutdown(window, api)

    def on_ready():
        from dariusai.os_integration import apply_window_icon
        from dariusai.viz.tray import start_tray_icon
        apply_window_icon()
        start_tray_icon(window, api)

    webview.start(on_ready, icon=str(ICON_PATH) if ICON_PATH.exists() else None)
    api.quit()  # the window is gone; nothing (tray, server, agent threads) outlives it


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # pythonw has no console to show a traceback in — write one to disk
        # instead of failing completely silently.
        import traceback
        (ROOT / "launch_error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
