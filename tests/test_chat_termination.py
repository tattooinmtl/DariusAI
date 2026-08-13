"""Tests for the chat-session loop termination paths.

The user's report: "the AI doesn't think anymore, stopped at 202".
The previous code had two ways to leave the chat panel's thinking
box open even though the agent had finished:

1. **Iteration cap without is_final.** The "Paused after N tool
   calls…" note was emitted with `is_final: False` (the default),
   so the chat panel never closed the thinking box. The user saw
   the count stop at 60+ iterations with a frozen "thinking…"
   label.

2. **Empty content blocks.** When the model returned text+tool_use
   blocks, the loop continued. When it returned only text, the
   loop emitted `is_final: True`. When it returned only tool_use, the
   loop continued. When it returned *neither* — empty content — the
   loop broke out without ever emitting a final assistant_text, so
   the thinking box also stayed open.

These tests pin the contract: the loop MUST emit a final assistant_text
with `is_final: True` on every exit path, so the chat panel always
closes the thinking box and the user sees an answer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _registry(tmp_path):
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore

    home = tmp_path / "brain"
    home.mkdir()
    return build_tool_registry(BrainStore(home))


def _count_by(events, type_):
    return [e for e in events if e.get("type") == type_]


def test_model_returns_text_with_no_tool_calls_emits_final(tmp_path):
    """Plain answer → break → emit a final assistant_text with
    is_final=True. The chat panel uses that flag to close the
    thinking box and render the message."""
    from dariusai.agent.chat import ChatSession
    from tests._stubs import ScriptedLLM, text_resp

    llm = ScriptedLLM(responses=[text_resp("the answer is 42")])
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    events: list[dict] = []
    session.send("?", on_event=events.append)

    finals = [e for e in _count_by(events, "assistant_text") if e.get("is_final")]
    assert len(finals) == 1, "exactly one final assistant_text expected"
    assert finals[0]["text"] == "the answer is 42"


def test_model_returns_empty_content_still_emits_final(tmp_path):
    """Empty content blocks (no text, no tool_use) used to leave the
    thinking box open — the loop exited without emitting. The fix:
    emit a final '(no response)' placeholder so the chat panel
    closes the thinking box visibly."""
    from dariusai.agent.chat import ChatSession

    class EmptyLLM:
        def complete(self, system, messages, tools=None):
            return {
                "content": [],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 0},
                "context_window": 200_000,
            }

        @property
        def context_window(self):
            return 200_000

    session = ChatSession(llm=EmptyLLM(), tools=_registry(tmp_path))
    events: list[dict] = []
    session.send("?", on_event=events.append)

    finals = [e for e in _count_by(events, "assistant_text") if e.get("is_final")]
    assert len(finals) == 1, "empty content must still emit exactly one final"
    assert "no response" in finals[0]["text"].lower()


def test_iteration_cap_emits_final_with_is_final_true(tmp_path):
    """The user-visible bug: the cap-note was emitted with
    is_final=False, so the chat panel never closed the thinking box.
    The fix: emit with is_final=True."""
    from dariusai.agent.chat import ChatSession, MAX_TOOL_ITERATIONS

    # A LLM that always wants another tool call → forces the loop to
    # hit MAX_TOOL_ITERATIONS.
    class AlwaysToolLLM:
        def __init__(self):
            self.calls = 0
        def complete(self, system, messages, tools=None):
            self.calls += 1
            return {
                "content": [{"type": "tool_use", "id": f"c{self.calls}",
                              "name": "list_projects", "input": {}}],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "context_window": 200_000,
            }
        @property
        def context_window(self):
            return 200_000

    session = ChatSession(llm=AlwaysToolLLM(), tools=_registry(tmp_path))
    events: list[dict] = []
    session.send("?", on_event=events.append)

    # The cap-note must be the FINAL event the chat panel sees.
    finals = [e for e in _count_by(events, "assistant_text") if e.get("is_final")]
    assert len(finals) == 1, "exactly one final event after the cap fires"
    assert "Paused after" in finals[0]["text"], "the cap note must say so"
    assert "continue" in finals[0]["text"], "the cap note must tell the user how to resume"
    # The cap was actually hit — number of LLM calls == iteration cap.
    # The loop runs until it hits MAX_TOOL_ITERATIONS or the model
    # stops emitting tool_use blocks.
    assert session.llm.calls == MAX_TOOL_ITERATIONS


def test_no_final_event_when_only_text_emitted_with_tool_use(tmp_path):
    """An intermediate text emitted WHILE the model still wants tool
    calls must NOT be marked final — the loop continues. The final
    flag is only set on the answer text once the model genuinely
    stops calling tools."""
    from dariusai.agent.chat import ChatSession

    class OneTextThenFinishLLM:
        def __init__(self):
            self.step = 0
        def complete(self, system, messages, tools=None):
            self.step += 1
            if self.step == 1:
                # First call: emit text + a tool call.
                return {
                    "content": [
                        {"type": "text", "text": "thinking..."},
                        {"type": "tool_use", "id": "c1", "name": "list_projects", "input": {}},
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "context_window": 200_000,
                }
            # Second call: just text, no tools → final answer.
            return {
                "content": [{"type": "text", "text": "the answer"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 5},
                "context_window": 200_000,
            }
        @property
        def context_window(self):
            return 200_000

    session = ChatSession(llm=OneTextThenFinishLLM(), tools=_registry(tmp_path))
    events: list[dict] = []
    session.send("?", on_event=events.append)

    text_events = _count_by(events, "assistant_text")
    # Two assistant_text events: the intermediate "thinking..." and
    # the final "the answer".
    assert len(text_events) == 2
    assert text_events[0]["text"] == "thinking..."
    assert text_events[0]["is_final"] is False, "intermediate text must not be marked final"
    assert text_events[1]["text"] == "the answer"
    assert text_events[1]["is_final"] is True, "final answer must be the only is_final=True event"
