"""Thin wrapper around the Anthropic SDK, normalized to plain dicts so
agent/graph.py's nodes never touch SDK-specific objects — makes them
trivially stubbable in tests without building fake Anthropic Message
objects. The normalized shape is still API-shaped (a list of
{"type": "text"|"tool_use", ...} blocks) so it round-trips straight back
into the next `messages` list unchanged, matching how the real API expects
multi-turn tool use to be threaded.

Both `AnthropicLLM` and `OpenAILLM` surface token usage + the model's
context window size so the chat panel can show real-time TPS and
"X / Y tokens used" against the model's window. The Protocol's
`complete()` returns `{"content": [...], "stop_reason": str,
"usage": {input_tokens, output_tokens}, "context_window": int}` —
`usage` and `context_window` are optional in the Protocol, so test
stubs that don't bother returning them still typecheck.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

DEFAULT_MODEL = os.environ.get("DARIUSAI_MODEL", "claude-sonnet-5")
MAX_TOKENS = int(os.environ.get("DARIUSAI_MAX_TOKENS", "4096"))
# Anthropic Claude Sonnet 4.5 default context window. Used by the chat
# panel's "X / Y tokens used" display so the user sees the percentage
# of the window that's left. Override at the LLM instance if needed.
DEFAULT_CONTEXT_WINDOW = 200_000


class LLM(Protocol):
    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Returns `{"content": [block, ...], "stop_reason": str,
        "usage": {"input_tokens": int, "output_tokens": int}?,
        "context_window": int?}`.

        `usage` and `context_window` are optional — the test stub
        returns only `content` and `stop_reason`, and the Protocol
        matches because they're not enforced. Production LLM impls
        fill both so the chat panel can show real-time stats.
        """
        ...

    @property
    def context_window(self) -> int:
        """Maximum tokens the model accepts in a single request.
        Reported alongside `usage` so the panel can compute
        'X / Y (k% left)' without a separate config pass."""
        ...


def build_llm(store, model: str | None = None):
    """Return a client that can actually talk to the *active* provider.

    This is the routing that was missing. `AnthropicLLM` posts to
    `{base_url}/v1/messages`; NVIDIA, OpenRouter, OpenCode and Agnes-AI all
    expect `POST {root}/chat/completions` with a different request and
    response shape. Handing an Anthropic client an OpenAI-shaped endpoint
    produces a 404 that looks like a bad URL but is really a bad protocol —
    correcting the URL can't fix it, because the route it wants doesn't
    exist on those providers at any path.

    Falls back to Anthropic when no provider is configured, which is what
    ANTHROPIC_API_KEY-only setups have always done.
    """
    from .model_catalog import spec_for

    active = store.get_active_provider()
    if not active or not active["has_api_key"]:
        return AnthropicLLM(model=model)

    protocol = spec_for(active["name"]).protocol
    if protocol == "anthropic":
        return AnthropicLLM.from_store(store, model=model)

    from .openai_llm import OpenAILLM
    return OpenAILLM(
        model=model or active["model"] or "",
        api_key=store.get_provider_api_key(active["name"]),
        # Fall back to the preset's own base URL when the provider row has
        # none: an empty string would normalise to api.openai.com, silently
        # sending a MiniMax key to OpenAI.
        base_url=active["base_url"] or spec_for(active["name"]).base_url,
    )


class AnthropicLLM:
    """Production LLM. Not exercised by the test suite (no network/API key
    available there); agent/graph.py's control flow is verified against a
    stub instead."""

    context_window: int = DEFAULT_CONTEXT_WINDOW

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None, context_window: int | None = None):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
        self.model = model or DEFAULT_MODEL
        if context_window is not None:
            self.context_window = context_window

    @classmethod
    def from_store(cls, store, model: str | None = None) -> "AnthropicLLM":
        """Prefer the Settings panel's active provider (brain.db, DPAPI-
        encrypted key) over ANTHROPIC_API_KEY — the env var still works as a
        fallback for anyone who'd rather not use the UI at all. Only an
        Anthropic-API-shaped endpoint (Anthropic itself, or a compatible
        gateway) actually works through this client; a provider pointed at
        a different wire protocol (e.g. raw OpenAI) would need a different
        LLM implementation, not just a different base_url."""
        active = store.get_active_provider()
        if active and active["has_api_key"]:
            api_key = store.get_provider_api_key(active["name"])
            return cls(
                model=model or active["model"] or None,
                api_key=api_key,
                base_url=active["base_url"] or None,
            )
        return cls(model=model)

    def complete(  # noqa: D102 — contract documented on the LLM Protocol
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tools or [],
        )
        content = []
        for block in resp.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        # Anthropic returns `usage` with `input_tokens` and
        # `output_tokens`. We forward both so the chat session can sum
        # them across a multi-turn call loop.
        usage = {}
        if getattr(resp, "usage", None) is not None:
            usage = {
                "input_tokens": getattr(resp.usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(resp.usage, "output_tokens", 0) or 0,
            }
        return {
            "content": content,
            "stop_reason": resp.stop_reason,
            "usage": usage,
            "context_window": self.context_window,
        }

