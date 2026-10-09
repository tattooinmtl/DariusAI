"""Phase 6 (2026-10-09): tools a coding agent needs.

edit_file (exact replacement instead of rewriting whole files),
search_files / glob_files (code search without the shell), parallel
read-only tool calls, and a per-turn "changed N files" card with Undo.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from dariusai.agent.chat import ChatSession
from dariusai.agent.sandbox import Sandbox
from dariusai.agent.tools import build_tool_registry
from dariusai.brain.store import BrainStore
from dariusai.viz.server import create_app


def _reg(tmp_path):
    store = BrainStore(tmp_path / "brain")
    proj = tmp_path / "proj"
    proj.mkdir()
    return build_tool_registry(store, Sandbox(root=proj)), proj


def test_edit_file_replaces_exact_text_and_refuses_ambiguity(tmp_path):
    reg, proj = _reg(tmp_path)
    (proj / "a.py").write_bytes(b"x = 1\r\ny = 1\r\n")
    assert reg.call("edit_file", {"path": "a.py", "old_string": "= 1", "new_string": "= 2"}).startswith("ERROR")
    out = reg.call("edit_file", {"path": "a.py", "old_string": "x = 1\ny = 1", "new_string": "x = 1\ny = 2"})
    assert out.startswith("edited a.py")
    assert (proj / "a.py").read_bytes() == b"x = 1\r\ny = 2\r\n"          # CRLF kept
    out = reg.call("edit_file", {"path": "a.py", "old_string": "1", "new_string": "3", "replace_all": True})
    assert "replaced 1 occurrence" in out or "replaced" in out
    assert "not found" in reg.call("edit_file", {"path": "a.py", "old_string": "zzz", "new_string": "q"})
    assert reg.call("edit_file", {"path": "missing.py", "old_string": "a", "new_string": "b"}).startswith("ERROR")


def test_search_and_glob_skip_dependency_folders(tmp_path):
    reg, proj = _reg(tmp_path)
    (proj / "src").mkdir()
    (proj / "src" / "app.py").write_text("def handler():\n    return 42\n")
    (proj / "node_modules").mkdir()
    (proj / "node_modules" / "lib.py").write_text("def handler(): pass\n")
    out = reg.call("search_files", {"pattern": r"def handler"})
    assert out == "src/app.py:1: def handler():"
    assert reg.call("glob_files", {"pattern": "**/*.py"}) == "src/app.py"
    assert "bad regex" in reg.call("search_files", {"pattern": "("})


def _resp(*blocks):
    return {"content": list(blocks), "usage": {"input_tokens": 1, "output_tokens": 1}}


class _ReadTwiceLLM:
    """First response: two read_file calls at once. Second: done."""
    def __init__(self):
        self.n = 0

    def complete(self, system, messages, tools=None):
        self.n += 1
        if self.n == 1:
            return _resp({"type": "tool_use", "id": "r1", "name": "read_file", "input": {"path": "a.txt"}},
                         {"type": "tool_use", "id": "r2", "name": "read_file", "input": {"path": "b.txt"}})
        return _resp({"type": "text", "text": "read both"})


def test_read_only_calls_run_in_parallel(tmp_path, monkeypatch):
    reg, proj = _reg(tmp_path)
    (proj / "a.txt").write_text("A"); (proj / "b.txt").write_text("B")
    from dariusai.agent import tools as T
    inside, peak, lock = [0], [0], threading.Lock()
    real = T._read_file

    def slow_read(sandbox, path):
        with lock:
            inside[0] += 1; peak[0] = max(peak[0], inside[0])
        time.sleep(0.2)
        with lock:
            inside[0] -= 1
        return real(sandbox, path)
    monkeypatch.setattr(T, "_read_file", slow_read)
    reg = build_tool_registry(BrainStore(tmp_path / "brain2"), Sandbox(root=proj))
    session = ChatSession(llm=_ReadTwiceLLM(), tools=reg)
    events = []
    session.send("read a and b", events.append)
    assert peak[0] == 2, "the two reads should overlap"
    results = [e["result"] for e in events if e.get("type") == "tool_call_result"]
    assert results == ["A", "B"]                                   # order kept
    tool_results = session.messages[-2]["content"]
    assert [r["tool_use_id"] for r in tool_results] == ["r1", "r2"]


class _EditLLM:
    def __init__(self):
        self.n = 0

    def complete(self, system, messages, tools=None):
        self.n += 1
        if self.n == 1:
            return _resp({"type": "tool_use", "id": "e1", "name": "edit_file",
                          "input": {"path": "app.py", "old_string": "return 1", "new_string": "return 2"}},
                         {"type": "tool_use", "id": "w1", "name": "write_file",
                          "input": {"path": "notes.md", "content": "hello\n"}})
        return _resp({"type": "text", "text": "done"})


def test_turn_changes_card_and_undo_over_the_socket(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "app.py").write_text("def f():\n    return 1\n")
    app = create_app(tmp_path / "brain", project_dir=proj, llm=_EditLLM())
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        assert ws.receive_json()["type"] == "session_loaded"
        ws.send_text("change it")
        card = None
        while card is None:
            m = ws.receive_json()
            if m.get("type") == "turn_changes":
                card = m
        files = {f["path"]: f for f in card["files"]}
        assert files["app.py"]["status"] == "modified" and files["app.py"]["added"] == 1
        assert "-    return 1" in files["app.py"]["diff"] and "+    return 2" in files["app.py"]["diff"]
        assert files["notes.md"]["status"] == "added"
        assert "return 2" in (proj / "app.py").read_text()

        ws.send_text('{"type": "undo_turn", "turn": "%s"}' % card["turn"])
        res = ws.receive_json()
        assert res["type"] == "undo_result" and sorted(res["restored"]) == ["app.py", "notes.md"]
        assert (proj / "app.py").read_text() == "def f():\n    return 1\n"
        assert not (proj / "notes.md").exists()

        ws.send_text('{"type": "undo_turn", "turn": "%s"}' % card["turn"])     # only once
        assert "error" in ws.receive_json()


def test_undo_keeps_a_file_changed_after_the_turn(tmp_path):
    from dariusai.agent.checkpoints import ChangeTracker
    ct = ChangeTracker()
    f = tmp_path / "x.txt"
    f.write_text("v1")
    ct.begin(tmp_path)
    ct.record(f); f.write_text("v2")
    card = ct.finish()
    f.write_text("v3 — the user's own edit")
    res = ct.undo(card["turn"])
    assert res["skipped"] == ["x.txt"] and f.read_text().startswith("v3")


def test_doctrine_points_at_the_new_tools():
    from dariusai.agent.doctrine import DOCTRINE
    assert "edit_file" in DOCTRINE and "search_files" in DOCTRINE and "glob_files" in DOCTRINE
