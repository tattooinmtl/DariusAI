"""0.98: saved chat sessions, Stop, permission requests answerable from the
tray, and the tray menu itself.

The user's asks (2026-10-09): a closed app must not restart from zero; a
Sessions list with the last five, a full list with delete and stars; a
tray menu with "— Darius AI —", Stop, Reload, Open project, permission
requests (only active ones, with a notification), Exit.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from dariusai.agent.chat import ChatSession
from dariusai.brain.store import BrainStore
from dariusai.viz.permissions import PermissionCenter
from dariusai.viz.server import create_app


# ---- sessions in the store -------------------------------------------------

def test_sessions_round_trip_star_and_delete(tmp_path):
    store = BrainStore(tmp_path / "brain")
    store.save_chat_session("a1", "first", [{"role": "user", "content": "hi"}],
                            [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}])
    store.save_chat_session("b2", "second", [], [{"role": "user", "text": "yo"}])
    listed = store.list_chat_sessions()
    assert [s["id"] for s in listed] == ["b2", "a1"]          # most recent first
    assert listed[1]["turns"] == 1 and listed[1]["favorite"] is False
    assert [s["id"] for s in store.list_chat_sessions(1)] == ["b2"]

    assert store.set_chat_session_favorite("a1", True)
    assert store.get_chat_session("a1")["favorite"] is True
    assert store.get_chat_session("a1")["messages"] == [{"role": "user", "content": "hi"}]

    assert store.delete_chat_session("b2")
    assert not store.delete_chat_session("b2")
    assert [s["id"] for s in store.list_chat_sessions()] == ["a1"]


class _EchoLLM:
    def complete(self, system, messages, tools=None):
        last = messages[-1]["content"]
        return {"content": [{"type": "text", "text": f"echo: {last}"}], "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_chat_socket_saves_and_resumes_the_latest_session(tmp_path):
    app = create_app(tmp_path / "brain", project_dir=tmp_path, llm=_EchoLLM())
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        first = ws.receive_json()
        assert first["type"] == "session_loaded" and first["display"] == []
        ws.send_text("remember pineapple")
        while True:
            m = ws.receive_json()
            if m.get("type") == "assistant_text" and m.get("is_final"):
                break
    # a new connection (app restarted / window reloaded) resumes it
    with client.websocket_connect("/ws/chat") as ws:
        again = ws.receive_json()
        assert again["type"] == "session_loaded" and again["id"] == first["id"]
        assert [d["text"] for d in again["display"]] == ["remember pineapple", "echo: remember pineapple"]
        ws.send_text('{"type": "session_new"}')
        fresh = ws.receive_json()
        assert fresh["type"] == "session_loaded" and fresh["id"] != first["id"] and fresh["display"] == []
    sessions = client.get("/api/sessions").json()
    assert [s["title"] for s in sessions] == ["remember pineapple"]
    assert client.put(f"/api/sessions/{first['id']}/favorite", json={"favorite": True}).json()["favorite"] is True
    assert client.delete(f"/api/sessions/{first['id']}").status_code == 200
    assert client.get("/api/sessions").json() == []


# ---- stop ------------------------------------------------------------------

class _ToolLoopLLM:
    """Keeps asking for list_dir until told to stop."""
    def __init__(self, session_ref):
        self.session_ref, self.calls = session_ref, 0

    def complete(self, system, messages, tools=None):
        self.calls += 1
        if self.calls == 2:
            self.session_ref[0].cancel()          # Stop pressed while the model was thinking
        return {"content": [{"type": "tool_use", "id": f"t{self.calls}", "name": "list_dir", "input": {"path": "."}}],
                "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_stop_ends_the_turn_with_a_final_message(tmp_path):
    from dariusai.agent.sandbox import Sandbox
    from dariusai.agent.tools import build_tool_registry

    store = BrainStore(tmp_path / "brain")
    ref = [None]
    session = ChatSession(llm=_ToolLoopLLM(ref), tools=build_tool_registry(store, Sandbox(root=tmp_path)))
    ref[0] = session
    events = []
    session.send("loop forever", events.append)
    finals = [e for e in events if e.get("type") == "assistant_text" and e.get("is_final")]
    assert len(finals) == 1 and finals[0]["stopped"] is True and finals[0]["text"].startswith("Stopped.")
    assert session.llm.calls == 2                   # no third model call after Stop
    # every tool_use got a tool_result, so the history stays valid for the next turn
    last_results = session.messages[-1]["content"]
    assert last_results[0]["content"] == "skipped: the user pressed Stop"
    # and the next turn is not pre-cancelled
    session.llm = _EchoLLM()
    assert session.send("hello again") == "echo: hello again"


# ---- permissions -------------------------------------------------------------

def test_permission_center_first_answer_wins_and_tells_the_window():
    center = PermissionCenter()
    got, settled = [], []
    center.add("r1", "D:/ref", "study it", got.append, settled.append)
    assert [r["id"] for r in center.active()] == ["r1"]
    assert "_resolve" not in center.active()[0]
    assert center.resolve("r1", True) is True
    assert got == [True] and settled == ["r1"]
    assert center.resolve("r1", False) is False       # already answered elsewhere
    assert center.active() == []


def test_permission_endpoints(tmp_path):
    app = create_app(tmp_path / "brain", project_dir=tmp_path, llm=_EchoLLM())
    client = TestClient(app)
    answered = threading.Event()
    app.state.permissions.add("abc", "D:/ref", "why", lambda allow: answered.set())
    assert [r["id"] for r in client.get("/api/permissions").json()] == ["abc"]
    assert client.post("/api/permissions/abc", json={"allow": True}).status_code == 200
    assert answered.is_set()
    assert client.post("/api/permissions/abc", json={"allow": True}).status_code == 404


# ---- tray ------------------------------------------------------------------

def test_tray_menu_and_permission_watch(monkeypatch):
    pytest = __import__("pytest")
    pytest.importorskip("pystray")
    from dariusai.viz import tray

    pending = [{"id": "r9", "path": r"C:\work\refproj", "reason": "x", "created_at": 1}]
    calls = []

    def fake_http(base, method, path, body=None, timeout=4.0):
        calls.append((method, path, body))
        if path == "/api/permissions":
            return list(pending)
        if path.startswith("/api/permissions/"):
            pending.clear()
        return {"stopped": 1}

    monkeypatch.setattr(tray, "_http", fake_http)

    class Icon:
        title = "DariusAI"
        def __init__(self): self.notes = []
        def notify(self, msg, title=""): self.notes.append(title)
        def update_menu(self): pass

    ctl = tray.TrayController(window=None, api=None, base_url="http://x")
    ctl.icon = Icon()
    menu = ctl.build_menu()
    texts = lambda: [i.text(i) if callable(i.text) else i.text for i in menu.items if i.visible]
    assert texts()[0] == "— Darius AI —"
    assert "Permission requests (0)" not in texts()          # hidden while nothing waits

    ctl.poll_once()
    assert ctl.icon.notes == ["Permission request"]
    assert "Permission requests (1)" in texts()
    assert "1 permission request waiting" in ctl.icon.title

    ctl.answer("r9", True)
    assert ("POST", "/api/permissions/r9", {"allow": True}) in calls
    assert ctl.requests == [] and "Permission requests (0)" not in texts()

    ctl.stop_turn()
    assert ("POST", "/api/chat/stop", {}) in calls


def test_tray_icon_files_exist():
    static = Path(__file__).resolve().parents[1] / "src/dariusai/viz/static"
    assert (static / "tray.png").stat().st_size > 1000
    assert (static / "tray.ico").stat().st_size > 1000
