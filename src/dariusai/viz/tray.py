"""System tray icon (Windows notification area, near the clock) — keeps
DariusAI running in the background per "keep the app open in tray":
*minimizing* hides the window to the tray instead of to the taskbar.
Closing does not: ✕, File -> Exit and the tray's Exit all end the process
outright (see DesktopAPI.quit), because a close button that silently
leaves a live process behind means one more of them every time the app is
opened. Tray menu: Maximize (default action — also fires on double-click),
Settings, Exit.

Built best-effort: if pystray/Pillow aren't available or icon creation
fails for any reason, the app still runs — window controls just fall back
to plain minimize/close instead of hide-to-tray (see DesktopAPI).
"""

from __future__ import annotations

import threading


def build_tray_icon(window, api):
    import pystray
    from PIL import Image

    from .server import STATIC_DIR

    image = Image.open(STATIC_DIR / "favicon.png")

    def on_maximize(icon, item):
        window.show()
        window.restore()
        window.maximize()
        api._maximized = True

    def on_settings(icon, item):
        window.show()
        window.restore()
        window.evaluate_js("window.__openSettingsPanel && window.__openSettingsPanel()")

    def on_exit(icon, item):
        api.quit()  # same shutdown as ✕ and File -> Exit — one path, no leaked processes

    menu = pystray.Menu(
        pystray.MenuItem("Maximize", on_maximize, default=True),
        pystray.MenuItem("Settings", on_settings),
        pystray.MenuItem("Exit", on_exit),
    )
    icon = pystray.Icon("DariusAI", image, "DariusAI", menu)
    api._icon = icon
    return icon


def start_tray_icon(window, api) -> None:
    """Best-effort — a tray failure must never take the whole app down
    with it. Runs pystray's own loop in a background thread since
    webview.start() already owns the main thread."""
    try:
        icon = build_tray_icon(window, api)
        threading.Thread(target=icon.run, daemon=True).start()
    except Exception:
        api._icon = None
