"""Hitting the tool-call cap must not read as a crash.

The bug: the cap was 12 — fewer calls than writing five files takes — and on
hitting it the notice *replaced* everything the model had narrated. A turn
that had just produced a working site reported only
"(hit the tool-call iteration cap for this turn)", and that string was what
got filed as the conversation's record of the turn.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import dariusai.agent.chat as chat
from dariusai.agent.chat import ChatSession
from dariusai.agent.sandbox import Sandbox
from dariusai.agent.tools import build_tool_registry
from dariusai.brain.store import BrainStore


class Endless:
    """Never stops calling tools, so the cap is always reached."""

    def __init__(self, text=True):
        self.n = 0
        self.text = text

    def complete(self, system, messages, tools=None):
        self.n += 1
        content = []
        if self.text:
            content.append({"type": "text", "text": f"step {self.n}"})
        content.append({"type": "tool_use", "id": f"t{self.n}", "name": "list_dir", "input": {"path": "."}})
        return {"content": content, "stop_reason": "tool_use"}


def _session(tmp_path, llm, saved=None):
    reg = build_tool_registry(BrainStore(tmp_path / "brain"), Sandbox(root=tmp_path))
    return ChatSession(llm=llm, tools=reg,
                       on_turn_complete=(lambda t: saved.update(t)) if saved is not None else None)


def test_the_cap_is_high_enough_for_real_work():
    """Five files is already five calls before any reading or verifying."""
    assert chat.MAX_TOOL_ITERATIONS >= 40


def test_cap_is_configurable(monkeypatch):
    monkeypatch.setenv("DARIUSAI_MAX_TOOL_ITERATIONS", "7")
    import importlib
    reloaded = importlib.reload(chat)
    try:
        assert reloaded.MAX_TOOL_ITERATIONS == 7
    finally:
        monkeypatch.delenv("DARIUSAI_MAX_TOOL_ITERATIONS", raising=False)
        importlib.reload(chat)


def test_narration_survives_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "MAX_TOOL_ITERATIONS", 4)
    out = _session(tmp_path, Endless()).send("build me a site")
    for step in ("step 1", "step 2", "step 3", "step 4"):
        assert step in out, "the model's own commentary was discarded"


def test_the_notice_says_what_happened_and_how_to_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "MAX_TOOL_ITERATIONS", 3)
    out = _session(tmp_path, Endless()).send("go")
    assert "3 tool calls" in out          # what got done
    assert "list_dir" in out              # which tools
    assert "continue" in out              # how to resume
    assert "saved" in out                 # that nothing was lost
    assert "DARIUSAI_MAX_TOOL_ITERATIONS" in out


def test_the_conversation_record_keeps_the_work_not_just_the_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "MAX_TOOL_ITERATIONS", 3)
    saved: dict = {}
    _session(tmp_path, Endless(), saved).send("go")
    assert "step 1" in saved["assistant_text"]
    assert len(saved["tool_results"]) == 3


def test_cap_with_no_text_does_not_crash(tmp_path, monkeypatch):
    """A model that calls tools silently still has to get a usable notice."""
    monkeypatch.setattr(chat, "MAX_TOOL_ITERATIONS", 2)
    out = _session(tmp_path, Endless(text=False)).send("go")
    assert "2 tool calls" in out


def test_a_normal_turn_is_unaffected(tmp_path):
    class Quick:
        def complete(self, system, messages, tools=None):
            return {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}

    assert _session(tmp_path, Quick()).send("hi") == "done"
