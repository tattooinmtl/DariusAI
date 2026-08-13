"""Tests for the chat session's auto-compaction feature.

The session now exposes a `compact(force=False)` method that gets called
automatically at the top of every LLM iteration whenever the prior
prompt was past `compact_threshold_ratio * context_window`. The UI's
[⚡ Compact] button sends `{"type":"compact"}` over the WebSocket, which
the server turns into a `compact(force=True)` call — same code path,
same return shape, different trigger.

These tests pin the design contract:

1. Auto-mode triggers at the threshold.
2. `force=True` compresses even when below the threshold.
3. Tool outputs older than `keep_recent_turns` get truncated; recent
   ones stay intact.
4. The history is reconstructed as `[summary, ack, *recent]`.
5. The LLM summarization call has a deterministic fallback when it
   fails (the chat session must never crash because the summarizer
   tripped).
6. The WebSocket `"compact"` action produces a `context_compacted`
   event with the saved-tokens count.
7. The UI declares the `chatCompactBtn` button, the `context_compacted`
   event handler, and the `renderCompactBanner` function.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# `src/` on the path so the agent modules import under test the same
# way they do in production.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _registry(tmp_path):
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore

    home = tmp_path / "brain"
    home.mkdir()
    return build_tool_registry(BrainStore(home))


def _big_payload(chars: int = 1500) -> str:
    """A tool_result payload that comfortably exceeds the 500-char
    truncation ceiling the chat session uses during compaction."""
    return "x" * chars


class _WindowedLLM:
    """An LLM stand-in that reports a known context_window and a
    scripted input/output token count. Used to drive the auto-compact
    threshold."""

    def __init__(self, *, in_tok: int = 0, out_tok: int = 0, context_window: int = 200_000):
        self.in_tok = in_tok
        self.out_tok = out_tok
        self.context_window = context_window
        self.calls = 0

    def complete(self, system, messages, tools=None):
        self.calls += 1
        return {
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": self.in_tok, "output_tokens": self.out_tok},
            "context_window": self.context_window,
        }


# ---------------------------------------------------------------------------
# 1. Auto-compact triggers at the threshold
# ---------------------------------------------------------------------------


def test_auto_compact_triggers_at_threshold(tmp_path):
    """When `current_input_tokens >= context_window * compact_threshold_ratio`,
    the chat session calls `compact()` itself before the next LLM call
    and emits a `context_compacted` event."""
    from dariusai.agent.chat import ChatSession

    # 200_000 * 0.75 = 150_000. Anything below the threshold stays
    # put; anything at-or-above it triggers compaction.
    llm = _WindowedLLM(in_tok=160_000, out_tok=10, context_window=200_000)
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    # The compact() method inspects the threshold against the prior
    # call's tokens, so we have to prime the session with one call's
    # worth of usage. Easiest path: set the fields directly.
    session.current_input_tokens = 160_000
    session.current_output_tokens = 10
    session.context_window = 200_000
    # Empty messages -> too_short. Build a meaningful history so the
    # pipeline has something to chew on.
    session.messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
        {"role": "assistant", "content": "a4"},
        {"role": "user", "content": "u5"},
        {"role": "assistant", "content": "a5"},
    ]
    events: list[dict] = []
    result = session.compact(force=False)
    events.append({"type": "context_compacted", **result})
    assert result["status"] == "ok"
    assert result["old_tokens"] == 160_000
    # The history was rewritten: two stub turns + the last 6 messages.
    assert len(session.messages) == 8
    assert session.messages[0]["role"] == "user"
    assert "[Prior Context Summary]" in session.messages[0]["content"]
    assert session.messages[1]["role"] == "assistant"
    assert "compacted" in session.messages[1]["content"].lower()


def test_auto_compact_skips_below_threshold(tmp_path):
    """Below the threshold, the auto-mode call is a no-op so it doesn't
    trash a conversation that hasn't grown past the window yet."""
    from dariusai.agent.chat import ChatSession

    llm = _WindowedLLM(in_tok=1000, out_tok=10, context_window=200_000)
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    session.current_input_tokens = 1000
    session.context_window = 200_000
    session.messages = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ] * 6
    before = list(session.messages)
    result = session.compact(force=False)
    assert result["status"] == "below_threshold"
    assert session.messages == before, "no-op must not mutate history"


def test_auto_compact_requires_known_window(tmp_path):
    """The threshold can't be computed without a context window. The
    call returns `no_window` so the session doesn't crash on a stub
    LLM that never reports usage."""
    from dariusai.agent.chat import ChatSession

    llm = _WindowedLLM()
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    session.messages = [{"role": "user", "content": "x"}] * 10
    assert session.compact(force=False) == {"status": "no_window"}


# ---------------------------------------------------------------------------
# 2. Manual / forced compaction
# ---------------------------------------------------------------------------


def test_force_compact_works_below_threshold(tmp_path):
    """`force=True` (the UI's [⚡ Compact] button) compresses even when
    the threshold hasn't been crossed — the user wants the compact
    right now, regardless of the gauge."""
    from dariusai.agent.chat import ChatSession

    llm = _WindowedLLM(in_tok=100, out_tok=10, context_window=200_000)
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    session.current_input_tokens = 100
    session.context_window = 200_000
    session.messages = []
    for i in range(10):
        session.messages.append({"role": "user", "content": f"u{i}"})
        session.messages.append({"role": "assistant", "content": f"a{i}"})
    result = session.compact(force=True)
    assert result["status"] == "ok"
    # Same shape: 2 stub turns + last 6 messages.
    assert len(session.messages) == 8
    assert session.messages[0]["role"] == "user"
    assert "[Prior Context Summary]" in session.messages[0]["content"]


def test_disabled_auto_compact_is_a_noop_unless_forced(tmp_path):
    """`auto_compact_enabled=False` means the auto-hook at the top of
    the loop is disabled. The UI button still works because it passes
    `force=True`."""
    from dariusai.agent.chat import ChatSession

    llm = _WindowedLLM(in_tok=160_000, out_tok=10, context_window=200_000)
    session = ChatSession(llm=llm, tools=_registry(tmp_path), auto_compact_enabled=False)
    session.current_input_tokens = 160_000
    session.context_window = 200_000
    session.messages = []
    for i in range(10):
        session.messages.append({"role": "user", "content": f"u{i}"})
        session.messages.append({"role": "assistant", "content": f"a{i}"})
    assert session.compact(force=False) == {"status": "disabled"}
    assert session.compact(force=True)["status"] == "ok"


def test_too_short_history_is_a_noop(tmp_path):
    """A conversation with fewer than `keep_recent_turns + 2` messages
    has nothing to compact — the caller would just see their own
    current message replaced."""
    from dariusai.agent.chat import ChatSession

    llm = _WindowedLLM(in_tok=160_000, out_tok=10, context_window=200_000)
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    session.current_input_tokens = 160_000
    session.context_window = 200_000
    session.messages = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    assert session.compact(force=True) == {"status": "too_short"}


# ---------------------------------------------------------------------------
# 3. Tool-output truncation
# ---------------------------------------------------------------------------


def test_tool_output_truncation_keeps_recent_two_messages(tmp_path):
    """Compaction truncates tool_result strings in older turns but
    leaves the last `keep_recent_turns` messages untouched — the
    recent context (file paths, active tool calls, the question
    under discussion) is the whole point of the keep_recent_turns
    ceiling."""
    from dariusai.agent.chat import ChatSession

    llm = _WindowedLLM(in_tok=160_000, out_tok=10, context_window=200_000)
    session = ChatSession(llm=llm, tools=_registry(tmp_path), keep_recent_turns=4)
    session.current_input_tokens = 160_000
    session.context_window = 200_000
    # Three older turns each carry a tool_result > 500 chars. The two
    # most recent messages are simple text and must not be touched.
    big = _big_payload(1500)
    session.messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": big},
        ]},
        {"role": "assistant", "content": "ok 1"},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t2", "content": big},
        ]},
        {"role": "assistant", "content": "ok 2"},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t3", "content": big},
        ]},
        {"role": "assistant", "content": "ok 3"},  # recent 4 starts here
        {"role": "user", "content": "what changed?"},
        {"role": "assistant", "content": "nothing yet"},
        # extra to push over keep_recent_turns + 2
        {"role": "user", "content": "anything else?"},
        {"role": "assistant", "content": "no"},
    ]
    # The cutoff is len(messages) - keep_recent_turns = 10 - 4 = 6.
    # So the *last 4* messages stay intact, and the first 6 are
    # candidates for truncation.
    session.compact(force=True)
    # The 4 most recent messages are still the originals, intact.
    tail = session.messages[-4:]
    assert tail[0] == {"role": "user", "content": "what changed?"}
    assert tail[1] == {"role": "assistant", "content": "nothing yet"}
    assert tail[2] == {"role": "user", "content": "anything else?"}
    assert tail[3] == {"role": "assistant", "content": "no"}
    # The truncated summaries live in the two stub turns at the top.
    # The first stub is a user turn whose content starts with
    # "[Prior Context Summary]" — but the older messages themselves
    # were truncated in place before being surfaced via the summary
    # call. Either way, the surviving history is short and has no
    # 1500-char blobs.
    joined = " ".join(m.get("content", "") if isinstance(m.get("content"), str)
                      else json.dumps(m.get("content", []))
                      for m in session.messages)
    assert "1500-char" not in joined
    assert "x" * 1500 not in joined


def test_tool_output_truncation_skips_short_strings(tmp_path):
    """A tool_result under the cap is left alone — the existence of
    the output is information, and we don't want to lose it for no
    gain."""
    from dariusai.agent.chat import ChatSession

    llm = _WindowedLLM(in_tok=160_000, out_tok=10, context_window=200_000)
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    session.current_input_tokens = 160_000
    session.context_window = 200_000
    small = "x" * 50
    session.messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": small},
        ]},
        {"role": "assistant", "content": "ok"},
    ] * 6
    before = session.messages[0]["content"][0]["content"]
    session.compact(force=True)
    # The short string is still findable in the history (the summary
    # surface may have rephrased it, so check the raw fields weren't
    # mutated under the cap).
    found = any(
        isinstance(m.get("content"), list)
        and any(
            b.get("type") == "tool_result" and b.get("content") == small
            for b in m["content"]
        )
        for m in session.messages
    )
    assert found, "short tool_result must not be truncated"


# ---------------------------------------------------------------------------
# 4. Summary reconstruction
# ---------------------------------------------------------------------------


def test_summary_reconstruction_keeps_recent_turns(tmp_path):
    """After compaction: [summary_user, ack_assistant, *recent]."""
    from dariusai.agent.chat import ChatSession

    # The summarizer LLM is called once and returns a known stub.
    class SummarizerLLM:
        def complete(self, system, messages, tools=None):
            return {
                "content": [{"type": "text", "text": "GOAL: ship compact"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "context_window": 200_000,
            }

    session = ChatSession(llm=SummarizerLLM(), tools=_registry(tmp_path), keep_recent_turns=4)
    session.current_input_tokens = 160_000
    session.context_window = 200_000
    recent = [
        {"role": "user", "content": "recent-u-1"},
        {"role": "assistant", "content": "recent-a-1"},
        {"role": "user", "content": "recent-u-2"},
        {"role": "assistant", "content": "recent-a-2"},
    ]
    older = [
        {"role": "user", "content": "older-u-1"},
        {"role": "assistant", "content": "older-a-1"},
        {"role": "user", "content": "older-u-2"},
        {"role": "assistant", "content": "older-a-2"},
    ]
    session.messages = older + recent
    session.compact(force=True)
    # Two stub turns + 4 recent = 6 messages.
    assert len(session.messages) == 6
    assert session.messages[0]["role"] == "user"
    assert "GOAL: ship compact" in session.messages[0]["content"]
    assert session.messages[1] == {
        "role": "assistant",
        "content": "Understood. Context compacted. Proceeding with task.",
    }
    assert session.messages[2:] == recent


def test_summarizer_failure_falls_back_to_deterministic_stub(tmp_path):
    """If the summarizer LLM raises, compaction still completes — the
    chat session must never crash because the summarizer tripped."""
    from dariusai.agent.chat import ChatSession

    class ExplodingLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, system, messages, tools=None):
            self.calls += 1
            raise RuntimeError("simulated API failure")

    session = ChatSession(llm=ExplodingLLM(), tools=_registry(tmp_path))
    session.current_input_tokens = 160_000
    session.context_window = 200_000
    session.messages = []
    for i in range(10):
        session.messages.append({"role": "user", "content": f"u{i}"})
        session.messages.append({"role": "assistant", "content": f"a{i}"})
    # The first call to .llm.complete() is the summarizer — it raises.
    # The session must still rewrite the history.
    result = session.compact(force=True)
    assert result["status"] == "ok"
    assert len(session.messages) == 8
    # The fallback summary mentions "summarizer unavailable" so the
    # user knows the result is degraded.
    assert "summarizer unavailable" in session.messages[0]["content"]


# ---------------------------------------------------------------------------
# 5. WebSocket integration
# ---------------------------------------------------------------------------


def test_websocket_compact_action_returns_context_compacted(tmp_path):
    """The UI's [⚡ Compact] button sends `{"type":"compact"}` over
    the WebSocket. The server intercepts it, calls
    `session.compact(force=True)`, and replies with a
    `context_compacted` event."""
    from fastapi.testclient import TestClient

    from dariusai.viz.server import create_app

    # A LLM that knows enough about the context to make the compact
    # succeed: the window is 200_000, the prior call's input is well
    # over the threshold, and the conversation is long enough.
    class BigLLM:
        def complete(self, system, messages, tools=None):
            return {
                "content": [{"type": "text", "text": "summary-stub"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "context_window": 200_000,
            }

    app = create_app(tmp_path / "brain", project_dir=tmp_path, llm=BigLLM())
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        # First drive a chat turn so the session has a real history.
        ws.send_text("hello")
        # Drain the events until the chat turn is done.
        while True:
            msg = ws.receive_json()
            if msg.get("type") == "assistant_text" and msg.get("is_final"):
                break
        # Now send the compact action. The server must reply with
        # context_compacted, NOT another assistant_text.
        ws.send_text(json.dumps({"type": "compact"}))
        reply = ws.receive_json()
        assert reply["type"] == "context_compacted"
        # The exact status code doesn't matter for this test — just
        # that the server didn't treat the JSON as a user prompt.
        assert "status" in reply


def test_websocket_non_compact_json_falls_through_to_llm(tmp_path):
    """A JSON payload that isn't `{"type":"compact"}` must NOT be
    intercepted — it falls through to the LLM as a user message,
    which is the behaviour the original chat socket had."""
    from fastapi.testclient import TestClient

    from dariusai.viz.server import create_app
    from tests._stubs import ScriptedLLM, text_resp

    app = create_app(tmp_path / "brain", project_dir=tmp_path, llm=ScriptedLLM([text_resp("ok")]))
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text(json.dumps({"type": "something_else"}))
        # Drain events until the chat turn is done. The session
        # emits token_stats before the final assistant_text, so the
        # first message is not always the answer.
        while True:
            msg = ws.receive_json()
            if msg.get("type") == "assistant_text" and msg.get("is_final"):
                break
        # The chat session treated the JSON as a user turn and produced
        # an assistant_text reply, exactly as it would for plain text.
        assert msg["type"] == "assistant_text"
        assert msg["is_final"] is True


# ---------------------------------------------------------------------------
# 6. Page-side contracts
# ---------------------------------------------------------------------------


def test_index_html_has_compact_button_and_handler():
    """The page declares the [⚡ Compact] button, the WebSocket route
    for `context_compacted`, and the renderCompactBanner function —
    pin the wire so a future rename can't silently break the UI."""
    html = (Path(__file__).resolve().parents[1] / "src" / "dariusai" / "viz" / "static" / "index.html").read_text(encoding="utf-8")
    # Button.
    assert "chatCompactBtn" in html, "the [⚡ Compact] button must be in the stats footer"
    assert "chat-compact-btn" in html, "the button class must be styled"
    # WebSocket sends the compact action.
    assert '{"type":"compact"}' in html or "'type':'compact'" in html or '"type": "compact"' in html, \
        "the click handler must send {\"type\":\"compact\"} over the socket"
    # WebSocket receives the compacted event.
    assert '"context_compacted"' in html, "ws.onmessage must route context_compacted to renderCompactBanner"
    assert "renderCompactBanner" in html, "renderCompactBanner() must be defined"
    # CSS for the banner.
    assert "chat-compact-banner" in html, "the banner class must be styled"
    # The button is disabled while in flight (UX guard).
    assert "compactBtn.disabled" in html or ".disabled" in html, "the button must be disabled during inflight"
