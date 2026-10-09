"""Inline <think> reasoning must not swallow the answer.

The user's report (2026-10-09): "he doesn't think, the thinking is always 0,
no text" — with MiniMax and GLM. Both put their reasoning in the reply text
as `<think>…</think>`. The chat panel saw a paragraph opening with a tag,
classed it as HTML code and rendered an empty code block, so no answer was
ever visible and the thinking box stayed at 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent.chat import ChatSession, split_reasoning


def test_think_block_is_split_from_the_answer():
    reasoning, answer = split_reasoning(
        "<think>\nThe user just said hi. Respond briefly.\n</think>\nHi! What can I help you build?")
    assert reasoning == "The user just said hi. Respond briefly."
    assert answer == "Hi! What can I help you build?"


def test_reply_without_reasoning_is_untouched():
    assert split_reasoning("Hello there, my friend.") == ("", "Hello there, my friend.")


def test_unclosed_think_is_all_reasoning():
    assert split_reasoning("<think>still going") == ("still going", "")


def test_missing_opening_tag():
    assert split_reasoning("plan the reply</think>Done.") == ("plan the reply", "Done.")


def test_html_in_the_answer_survives():
    reasoning, answer = split_reasoning("<think>x</think>\n<div>real html</div>")
    assert reasoning == "x" and answer == "<div>real html</div>"


class _ThinkingLLM:
    """Replies the way MiniMax M-series does: reasoning inline in the text."""

    def complete(self, system, messages, tools=None):
        return {"content": [{"type": "text", "text": "<think>\nGreeting, no tools needed.\n</think>\nok"}],
                "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_session_sends_reasoning_to_the_thinking_box_and_answer_as_text(tmp_path):
    from dariusai.agent.sandbox import Sandbox
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore

    store = BrainStore(tmp_path / "brain")
    session = ChatSession(llm=_ThinkingLLM(), tools=build_tool_registry(store, Sandbox(root=tmp_path)))
    events = []
    session.send("reply with ok", events.append)

    reasoning = [e for e in events if e["type"] == "reasoning"]
    final = [e for e in events if e["type"] == "assistant_text" and e["is_final"]]
    assert [e["text"] for e in reasoning] == ["Greeting, no tools needed."]
    assert [e["text"] for e in final] == ["ok"]
    # the model's own history keeps its reasoning (MiniMax wants it back)
    assert "<think>" in session.messages[-1]["content"][0]["text"]
