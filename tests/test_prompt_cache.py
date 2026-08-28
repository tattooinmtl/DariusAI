"""Prompt caching — the largest single saving in the harness.

The doctrine + role prompt and the tool schemas are byte-identical on
every call, and one chat turn makes up to `MAX_TOOL_ITERATIONS` calls.
Without cache breakpoints that fixed prefix is re-sent at full price on
each of them.

What's pinned here:

1. `cacheable_system` puts one ephemeral breakpoint on the system block
   (which, by the API's tools -> system -> messages render order, caches
   the tool schemas too).
2. `with_cached_history` puts a rolling breakpoint on the last message
   and — critically — does not mutate the caller's list, because the
   ChatSession re-sends that same list next iteration.
3. `DARIUSAI_PROMPT_CACHE=0` turns both off.
4. The chat session counts cached tokens as part of the prompt size, so
   the context gauge and the auto-compaction threshold still see the
   real number.
5. Eviction holds small payloads back rather than invalidating the cache
   for a trivial saving.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _stubs import ScriptedLLM, text_resp  # noqa: E402


@pytest.fixture
def llm_mod():
    from dariusai.agent import llm
    return llm


# ---------------------------------------------------------------------------
# 1-2. Breakpoint placement
# ---------------------------------------------------------------------------


def test_system_prompt_gets_one_ephemeral_breakpoint(llm_mod):
    blocks = llm_mod.cacheable_system("YOU ARE A TEST")
    assert blocks == [{
        "type": "text", "text": "YOU ARE A TEST",
        "cache_control": {"type": "ephemeral"},
    }]


def test_empty_system_is_left_alone(llm_mod):
    assert llm_mod.cacheable_system("") == ""


def test_last_message_gets_the_rolling_breakpoint(llm_mod):
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": [{"type": "text", "text": "reply"}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": "output"},
        ]},
    ]
    out = llm_mod.with_cached_history(messages)
    assert out[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # Only the last block carries one — four breakpoints is the API's cap
    # and one of them is already spent on `system`.
    assert "cache_control" not in out[1]["content"][0]


def test_string_content_is_promoted_to_a_cached_block(llm_mod):
    out = llm_mod.with_cached_history([{"role": "user", "content": "hello"}])
    assert out[-1]["content"] == [{
        "type": "text", "text": "hello", "cache_control": {"type": "ephemeral"},
    }]


def test_the_callers_messages_are_never_mutated(llm_mod):
    """The ChatSession owns `messages` and re-sends the same list on the
    next iteration. A `cache_control` key accumulating on old blocks
    would burn breakpoints and change the very bytes the cache is keyed
    on — turning the optimisation into a permanent cache miss."""
    original = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    snapshot = repr(original)
    llm_mod.with_cached_history(original)
    assert repr(original) == snapshot


def test_empty_history_is_left_alone(llm_mod):
    assert llm_mod.with_cached_history([]) == []


# ---------------------------------------------------------------------------
# 3. The kill switch
# ---------------------------------------------------------------------------


def test_env_var_disables_caching(monkeypatch):
    """An Anthropic-compatible gateway that rejects `cache_control` is the
    reason this switch exists."""
    monkeypatch.setenv("DARIUSAI_PROMPT_CACHE", "0")
    from dariusai.agent import llm as llm_mod
    reloaded = importlib.reload(llm_mod)
    try:
        assert reloaded.cacheable_system("sys") == "sys"
        msgs = [{"role": "user", "content": "hi"}]
        assert reloaded.with_cached_history(msgs) is msgs
    finally:
        monkeypatch.delenv("DARIUSAI_PROMPT_CACHE", raising=False)
        importlib.reload(reloaded)


# ---------------------------------------------------------------------------
# 4. The gauge and the compaction threshold still see the whole prompt
# ---------------------------------------------------------------------------


class _CachingLLM:
    """Reports usage the way the API does once caching is on: `input_tokens`
    is the uncached remainder only."""

    def __init__(self, input_tokens, cache_read, cache_write=0):
        self.usage = {
            "input_tokens": input_tokens,
            "output_tokens": 10,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        }

    def complete(self, system, messages, tools=None):
        return {
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": self.usage,
            "context_window": 200_000,
        }


def _session(llm, tmp_path):
    from dariusai.agent.chat import ChatSession
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore

    home = tmp_path / "brain"
    home.mkdir()
    return ChatSession(llm=llm, tools=build_tool_registry(BrainStore(home)))


def test_cached_tokens_count_towards_the_prompt_size(tmp_path):
    """Reading `input_tokens` alone would show a 60-iteration turn as
    using almost no context — the gauge would flatline and auto-compaction
    would never fire."""
    session = _session(_CachingLLM(input_tokens=500, cache_read=40_000, cache_write=1_000), tmp_path)
    session.send("go")
    assert session.current_input_tokens == 41_500
    assert session.current_cache_read_tokens == 40_000


def test_token_stats_expose_the_cache_hit_ratio(tmp_path):
    """A hit ratio stuck at zero is the only visible symptom of a silent
    cache invalidator, so the panel has to be able to show it."""
    session = _session(_CachingLLM(input_tokens=1_000, cache_read=9_000), tmp_path)
    events: list[dict] = []
    session.send("go", on_event=events.append)

    stats = [e for e in events if e["type"] == "token_stats"][-1]
    assert stats["cache_read_tokens"] == 9_000
    assert stats["cache_hit_ratio"] == pytest.approx(0.9)


def test_a_provider_without_cache_fields_still_works(tmp_path):
    """The test stub and any OpenAI-shaped provider omit them."""
    session = _session(ScriptedLLM([text_resp("hi")]), tmp_path)
    assert session.send("go") == "hi"
    assert session.current_cache_read_tokens == 0


def test_openai_usage_maps_onto_the_same_shape():
    """OpenAI reports cached tokens *inside* prompt_tokens; Anthropic
    reports them outside input_tokens. The mapping normalises to the
    Anthropic shape so the chat panel has one thing to render."""
    from dariusai.agent.openai_llm import OpenAILLM

    payload = {
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 10_000, "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 9_000},
        },
    }

    class _Response:
        status_code = 200

        def json(self):
            return payload

    llm = OpenAILLM(model="m", api_key="k", base_url="https://example.invalid/v1",
                    transport=lambda url, headers, body: _Response())
    usage = llm.complete(system="s", messages=[{"role": "user", "content": "hi"}])["usage"]
    assert usage["input_tokens"] == 1_000, "cached tokens must not be double-counted"
    assert usage["cache_read_input_tokens"] == 9_000


# ---------------------------------------------------------------------------
# 5. Eviction is cache-aware
# ---------------------------------------------------------------------------


def test_small_stale_payloads_are_held_rather_than_evicted(tmp_path):
    """Rewriting history invalidates the cache from that point. Dropping
    900 chars would cost more in cache misses than it saves in re-sent
    bytes, so eviction waits until the stale payloads add up."""
    session = _session(_CachingLLM(input_tokens=100, cache_read=0), tmp_path)
    session.skill_payload_ttl = 0
    session._skill_payloads["c1"] = {
        "name": "invoke_skill", "label": "tiny-skill", "iteration": 0, "chars": 900,
    }
    session.messages = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "x" * 900},
    ]}]
    assert session._evict_skill_payloads(5) == {}
    assert session.messages[0]["content"][0]["content"] == "x" * 900


def test_a_large_stale_payload_is_evicted(tmp_path):
    session = _session(_CachingLLM(input_tokens=100, cache_read=0), tmp_path)
    session.skill_payload_ttl = 0
    session._skill_payloads["c1"] = {
        "name": "invoke_skill", "label": "giant-skill", "iteration": 0, "chars": 26_000,
    }
    session.messages = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "x" * 26_000},
    ]}]
    result = session._evict_skill_payloads(5)
    assert result["saved_chars"] == 26_000
    assert "giant-skill" in session.messages[0]["content"][0]["content"]
