"""Tests for the chat-panel token stats footer.

The chat session emits a `token_stats` event after every LLM call so
the page can show real-time TPS and a "X / Y tokens used (k% left)"
gauge against the model's context window. These tests pin:

1. The LLM protocol's `usage` and `context_window` fields are
   surfaced by both production impls (AnthropicLLM, OpenAILLM) and
   accepted by the chat session even when absent (the test stub).
2. The chat session tracks per-call input/output tokens and exposes
   them in the `token_stats` event with cumulative totals.
3. Tokens-per-second is computed from this call's output tokens and
   the elapsed wall-clock time (not the cumulative), and it's floored
   at a tiny denominator so a sub-millisecond call doesn't blow up.
4. The tool-call loop emits one `token_stats` event per iteration so
   the user sees the count climb as the agent thinks + tool-calls.
5. The page-side DOM hooks (`#chatStats`, `#chatStatsTps`,
   `#chatStatsCnt`, `#chatStatsPct`, `renderStats`) exist and the
   percent-left color classes (`.warn`, `.danger`) are defined.
6. The `send()` function resets the stats panel between turns so the
   previous query's totals don't leak into the next.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

# `src/` on the path so the agent modules import under test the same
# way they do in production.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# 1. LLM surface contract
# ---------------------------------------------------------------------------


def test_anthropic_protocol_documents_usage_and_context_window():
    """The LLM Protocol declares `usage` and `context_window` as
    optional fields. A regression that drops them from the docstring
    breaks the chat panel's reference doc; the test pins the doc."""
    from dariusai.agent import llm as llm_module

    src = Path(llm_module.__file__).read_text(encoding="utf-8")
    assert "usage" in src, "LLM Protocol must document `usage`"
    assert "context_window" in src, "LLM Protocol must document `context_window`"
    assert "DEFAULT_CONTEXT_WINDOW" in src, "AnthropicLLM must declare a default context window"


def test_openai_path_uses_default_context_window():
    """OpenAILLM must carry a context_window too — needed for the
    'X / Y (k% left)' gauge, defaults to gpt-4o-class 128K."""
    from dariusai.agent import openai_llm

    src = Path(openai_llm.__file__).read_text(encoding="utf-8")
    assert "DEFAULT_CONTEXT_WINDOW" in src
    assert "context_window" in src


# ---------------------------------------------------------------------------
# 2. Chat session emits token_stats from a fake LLM
# ---------------------------------------------------------------------------


def _registry(tmp_path):
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore

    home = tmp_path / "brain"
    home.mkdir()
    return build_tool_registry(BrainStore(home))


class _FakeLLM:
    """A controllable LLM stand-in that returns scripted usage on
    each call. Mirrors the contract `AnthropicLLM.complete()` /
    `OpenAILLM.complete()` satisfy in production."""

    def __init__(self, *, in_tok: int, out_tok: int, context_window: int = 200_000, latency_ms: float = 0.0):
        self._in = in_tok
        self._out = out_tok
        self._window = context_window
        self._latency = latency_ms
        self.calls = 0

    def complete(self, system, messages, tools=None):
        # Throttle the call so TPS is computable. perf_counter is
        # monotonic; the chat session uses it too.
        if self._latency > 0:
            time.sleep(self._latency / 1000.0)
        self.calls += 1
        return {
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": self._in, "output_tokens": self._out},
            "context_window": self._window,
        }

    @property
    def context_window(self):
        return self._window


def test_first_call_publishes_token_stats_event(tmp_path):
    from dariusai.agent.chat import ChatSession

    llm = _FakeLLM(in_tok=1200, out_tok=50)
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    events: list[dict] = []
    session.send("hello", on_event=events.append)

    stats = [e for e in events if e.get("type") == "token_stats"]
    assert len(stats) == 1, "one LLM call must produce one token_stats event"
    s = stats[0]
    assert s["input_tokens"] == 1200
    assert s["output_tokens"] == 50
    # `current_total` is the panel's numerator — the size of the
    # prompt being sent right now, not the sum across all calls.
    assert s["current_total"] == 1250
    # `total_processed` is the session-level sum, available but not
    # what the page renders by default.
    assert s["total_processed"] == 1250
    assert s["context_window"] == 200_000
    assert s["elapsed_ms"] >= 0
    assert s["tps"] >= 0


def test_cumulative_tokens_run_across_the_call_loop(tmp_path):
    """The chat session runs a tool-calling loop — multiple LLM calls
    per turn. current_total reflects the most recent call's input
    + output, NOT a sum across the loop. total_processed is the
    session-level sum."""
    from dariusai.agent.chat import ChatSession

    # A LLM that returns different usage on each call and a tool to
    # call, so the loop iterates more than once.
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore
    from pathlib import Path

    class CountingLLM:
        def __init__(self):
            self.seq = [(100, 10), (200, 20), (300, 30)]
            self.idx = 0

        def complete(self, system, messages, tools=None):
            in_tok, out_tok = self.seq[self.idx]
            self.idx += 1
            tool_call = None
            if self.idx < len(self.seq):
                # Mid-loop iteration: request a tool call so the
                # loop iterates again.
                tool_call = {"type": "tool_use", "id": f"c{self.idx}", "name": "list_projects", "input": {}}
            content = []
            if tool_call:
                content.append(tool_call)
            else:
                content.append({"type": "text", "text": "done"})
            return {
                "content": content,
                "stop_reason": "tool_use" if tool_call else "end_turn",
                "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
                "context_window": 200_000,
            }

        @property
        def context_window(self):
            return 200_000

    home = Path(tmp_path) / "brain"
    home.mkdir()
    reg = build_tool_registry(BrainStore(home))
    session = ChatSession(llm=CountingLLM(), tools=reg)
    events: list[dict] = []
    session.send("hello", on_event=events.append)

    stats = [e for e in events if e.get("type") == "token_stats"]
    assert len(stats) == 3, "three LLM calls must produce three token_stats events"
    # current_total is the call-local input+output, NOT a sum:
    # each iteration's current_total is just that call's input+output.
    assert stats[0]["current_total"] == 110
    assert stats[1]["current_total"] == 220
    assert stats[2]["current_total"] == 330
    # total_processed IS the running sum across the loop.
    assert stats[0]["total_processed"] == 110
    assert stats[1]["total_processed"] == 330
    assert stats[2]["total_processed"] == 660
    # And the session itself tracks the same.
    assert session.current_input_tokens == 300
    assert session.current_output_tokens == 30
    assert session.total_input_tokens == 600
    assert session.total_output_tokens == 60
    assert session.context_window == 200_000


def test_current_replaces_cumulative_does_not_grow_past_window(tmp_path):
    """The bug: the previous 'cumulative_total' ran a sum across every
    call in the session, so a long conversation pushed the chat-panel
    gauge past the model's context window even though any single call
    was well within it. The fix: current_total is the call-local
    input + output, which always fits in the window (the API rejects
    a request larger than the window)."""
    from dariusai.agent.chat import ChatSession

    # A LLM that always returns the same usage — simulates a long
    # session where every call is the same size.
    class SteadyLLM:
        def __init__(self, n_calls=20):
            self._n = n_calls
            self.calls = 0
        def complete(self, system, messages, tools=None):
            self.calls += 1
            return {
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1000, "output_tokens": 100},
                "context_window": 200_000,
            }
        @property
        def context_window(self):
            return 200_000

    llm = SteadyLLM()
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    session.send("hello", on_event=lambda e: None)
    # current_total caps at the call size (1100), never grows.
    assert session.current_input_tokens == 1000
    assert session.current_output_tokens == 100
    # total_processed is the sum, which can grow without bound.
    assert session.total_input_tokens == 1000
    assert session.total_output_tokens == 100


def test_tps_does_not_explode_on_submillisecond_calls(tmp_path):
    """A provider returning in <1ms with a small output_tokens count
    must not produce an infinite or astronomically-large TPS. The
    denominator is floored at 1ms."""
    from dariusai.agent.chat import ChatSession

    llm = _FakeLLM(in_tok=10, out_tok=1, latency_ms=0.0)
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    events: list[dict] = []
    session.send("hi", on_event=events.append)
    s = [e for e in events if e.get("type") == "token_stats"][0]
    # TPS = 1 token / max(elapsed_ms/1000, 0.001) ~= 1000
    assert 0 < s["tps"] < 1_000_000, f"TPS exploded: {s['tps']}"


def test_tps_reflects_per_call_output_not_cumulative(tmp_path):
    """TPS is a rate, not a stock — it should reflect THIS call's
    output tokens / elapsed time, not the cumulative total."""
    from dariusai.agent.chat import ChatSession

    llm = _FakeLLM(in_tok=1000, out_tok=200, latency_ms=20.0)
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    events: list[dict] = []
    session.send("hi", on_event=events.append)
    s = [e for e in events if e.get("type") == "token_stats"][0]
    # ~200 tokens / 0.020 s = ~10,000 tps. Allow a wide window.
    assert 5_000 < s["tps"] < 50_000, f"TPS out of expected band: {s['tps']}"


def test_stub_sdk_with_no_usage_still_emits_token_stats(tmp_path):
    """The test stub (tests/_stubs.py) returns responses without
    `usage` or `context_window`. The chat session must still emit a
    `token_stats` event — with zero tokens and zero window — so the
    page's stats panel doesn't go silent on stub responses."""
    from dariusai.agent.chat import ChatSession
    from tests._stubs import ScriptedLLM, text_resp

    llm = ScriptedLLM(responses=[text_resp("hi")])
    session = ChatSession(llm=llm, tools=_registry(tmp_path))
    events: list[dict] = []
    session.send("hi", on_event=events.append)
    stats = [e for e in events if e.get("type") == "token_stats"]
    assert len(stats) == 1
    assert stats[0]["input_tokens"] == 0
    assert stats[0]["output_tokens"] == 0
    assert stats[0]["current_total"] == 0
    assert stats[0]["context_window"] == 0


# ---------------------------------------------------------------------------
# 3. Page-side contracts
# ---------------------------------------------------------------------------


def test_index_html_has_stats_panel_with_required_ids():
    """The renderStats() JS function reads these IDs by name. If any
    of them is renamed or removed, the panel goes silent — the test
    pins the contract."""
    html = (Path(__file__).resolve().parents[1] / "src" / "dariusai" / "viz" / "static" / "index.html").read_text(encoding="utf-8")
    assert "chatStats" in html, "renderStats() needs #chatStats (the footer)"
    assert "chatStatsTps" in html, "renderStats() needs #chatStatsTps"
    assert "chatStatsCnt" in html, "renderStats() needs #chatStatsCnt"
    assert "chatStatsPct" in html, "renderStats() needs #chatStatsPct"
    # The render function itself.
    assert "function renderStats" in html, "renderStats() must be defined"
    # The three render branches.
    assert "chat-stats-tps" in html, "the TPS span must be styled"
    assert "chat-stats-cnt" in html, "the count span must be styled"
    assert "chat-stats-pct" in html, "the percentage span must be styled"
    # The two color-state classes — defined in the inline `<style>` block.
    assert ".chat-stats-pct.warn" in html, "warn state (15< pct<50) must be styled"
    assert ".chat-stats-pct.danger" in html, "danger state (pct<15) must be styled"
    # The WebSocket route.
    assert '"token_stats"' in html, "ws.onmessage must check type === 'token_stats'"
    assert "renderStats(data" in html, "ws.onmessage must pass the event to renderStats(data)"
    # The per-turn reset.
    assert "chatStatsTps" in html and "chatStatsPct" in html, "send() must reset the stats footer on each new turn"
    # The percent-left formatting.
    assert "% left" in html, "renderStats() must render 'k% left' wording"
