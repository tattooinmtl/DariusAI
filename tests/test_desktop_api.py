import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.viz.window import DesktopAPI, attach_shutdown


class FakeWindow:
    def __init__(self):
        self.calls = []

    def destroy(self):
        self.calls.append("destroy")

    def minimize(self):
        self.calls.append("minimize")

    def maximize(self):
        self.calls.append("maximize")

    def restore(self):
        self.calls.append("restore")

    def hide(self):
        self.calls.append("hide")

    def show(self):
        self.calls.append("show")


def test_toggle_maximize_calls_maximize_then_restore():
    api = DesktopAPI(force_exit=lambda: None)
    api._window = FakeWindow()

    first = api.toggle_maximize()
    assert first is True
    assert api._window.calls == ["maximize"]

    second = api.toggle_maximize()
    assert second is False
    assert api._window.calls == ["maximize", "restore"]


def test_toggle_maximize_without_window_is_a_safe_noop():
    api = DesktopAPI(force_exit=lambda: None)  # _window is None — no create_window() happened
    result = api.toggle_maximize()
    assert result is False


class FakeIcon:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def test_close_and_minimize_delegate_to_window_without_tray():
    api = DesktopAPI(force_exit=lambda: None)  # no tray running (_icon is None) — real minimize/destroy
    api._window = FakeWindow()
    api.minimize()
    api.close()
    assert api._window.calls == ["minimize", "destroy"]


def test_close_quits_even_when_the_tray_is_running():
    """The bug this pins: ✕ used to hide to tray, so every close left a
    live process behind and they piled up one per launch."""
    api = DesktopAPI(force_exit=lambda: None)
    api._window = FakeWindow()
    api._icon = FakeIcon()
    icon = api._icon

    api.close()

    assert api._window.calls == ["destroy"]
    assert "hide" not in api._window.calls
    assert icon.stopped is True


def test_minimize_still_hides_to_tray_when_the_tray_is_running():
    api = DesktopAPI(force_exit=lambda: None)
    api._window = FakeWindow()
    api._icon = FakeIcon()
    api.minimize()
    assert api._window.calls == ["hide"]


def test_quit_is_idempotent_across_overlapping_exit_routes():
    """✕ destroys the window, which fires pywebview's `closed` event, which
    quits again — the second pass must be a no-op, not a second destroy on
    a dead window."""
    api = DesktopAPI(force_exit=lambda: None)
    api._window = FakeWindow()
    api._icon = FakeIcon()

    api.quit()
    api.quit()
    api.close()

    assert api._window.calls == ["destroy"]


def test_quit_survives_a_window_or_icon_that_raises():
    """A tray icon that's already stopped, or a window the OS closed out
    from under us, must not stop the rest of the shutdown."""
    class Exploding:
        def stop(self): raise RuntimeError("already stopped")
        def destroy(self): raise RuntimeError("already destroyed")

    api = DesktopAPI(force_exit=lambda: None)
    api._window = Exploding()
    api._icon = Exploding()
    api.quit()  # must not raise
    assert api._quitting is True


def test_quit_hard_exits_when_a_graceful_shutdown_does_not_finish():
    """The guarantee: closed means the process is gone. If destroying the
    window doesn't end the process (a stuck WebView2 pump, a thread that
    ignored the stop), the hard exit fires anyway."""
    fired = threading.Event()
    api = DesktopAPI(force_exit=fired.set, exit_grace=0.05)
    api._window = FakeWindow()

    api.quit()

    assert fired.wait(2.0), "force exit never fired — a hung window would leave the process alive"


def test_exit_timer_can_never_be_what_keeps_the_process_alive():
    api = DesktopAPI(force_exit=lambda: None, exit_grace=30)
    api._window = FakeWindow()
    api.quit()
    assert api._exit_timer.daemon is True


def test_attach_shutdown_routes_os_level_close_into_quit():
    """Alt-F4 / taskbar Close / End task never touch the page's ✕ button —
    pywebview's `closed` event is the only notice we get."""
    class FakeEvents:
        def __init__(self): self.closed = FakeEvent()

    class FakeEvent:
        def __init__(self): self.handlers = []
        def __iadd__(self, handler): self.handlers.append(handler); return self

    window = FakeWindow()
    window.events = FakeEvents()
    api = DesktopAPI(force_exit=lambda: None)
    api._window = window

    attach_shutdown(window, api)
    assert window.events.closed.handlers, "nothing subscribed to the OS close event"

    window.events.closed.handlers[0]()  # the OS closed the window
    assert api._quitting is True


def test_minimize_and_close_without_window_are_safe_noops():
    api = DesktopAPI(force_exit=lambda: None)  # _window is None
    api.minimize()
    api.close()  # must not raise
