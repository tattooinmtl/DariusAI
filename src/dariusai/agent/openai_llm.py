"""An LLM client for OpenAI-shaped providers — NVIDIA, OpenRouter, OpenCode,
Agnes-AI and the many gateways that copy that wire format.

Why this exists: the project had exactly one client, `AnthropicLLM`, which
posts to `{base_url}/v1/messages`. Point that at NVIDIA and you get a 404,
because NVIDIA speaks `POST /v1/chat/completions` with a different request
*and* response shape. No amount of correcting the base URL fixes a protocol
mismatch — which is why "wrong endpoint" and "wrong client" look identical
from the outside.

The rest of the codebase (graph.py, chat.py) is written against Anthropic's
normalised block shape, and there is no reason for that to change to
accommodate a second provider. So the translation lives here, in both
directions:

    tools      Anthropic {name, description, input_schema}
               -> OpenAI  {type: function, function: {..., parameters}}

    messages   Anthropic content blocks (text / tool_use / tool_result)
               -> OpenAI  assistant.tool_calls + role:"tool" messages

    response   OpenAI  choices[0].message (+ tool_calls)
               -> Anthropic blocks + stop_reason

Uses httpx directly rather than the `openai` SDK: httpx is already a
dependency, and this is one POST. Adding an SDK to send it would be a
dependency bought for nothing.
"""

from __future__ import annotations

import json
from typing import Any

from .model_catalog import normalize_base_url

DEFAULT_TIMEOUT = 180.0
DEFAULT_MAX_TOKENS = 4096
# OpenAI gpt-4o-class default context window. Override via the
# constructor for models with different limits (e.g. gpt-4.1-mini's
# 1M context, or smaller legacy models).
DEFAULT_CONTEXT_WINDOW = 128_000


def explain_provider_error(status: int, detail: str, model: str, url: str) -> str:
    """Turn a provider's error into one a person can act on.

    A catalogue lists what the *provider* hosts, which is not the same as
    what *your account* may call — NVIDIA answers a model you aren't
    entitled to with "Function <uuid>: Not found for account <id>", which
    reads like a bug in this app rather than a choice you can change. The
    raw text is always kept on the end; this only adds the next action.
    """
    lowered = detail.lower()
    if "not found for account" in lowered:
        hint = (f"the model {model!r} isn't enabled for your account with this provider — "
                "pick a different one from the model list in Settings")
    elif status == 401 or "unauthorized" in lowered or "invalid api key" in lowered:
        hint = "the API key was rejected — re-enter it in Settings"
    elif status == 404:
        hint = (f"{url} has no such route — check the Base URL in Settings "
                "(the API root, e.g. https://host/v1)")
    elif status == 429 or "rate limit" in lowered:
        hint = "the provider is rate-limiting this key — wait, or switch model/provider"
    elif status >= 500:
        hint = "the provider is failing on its side — retry, or switch provider"
    else:
        hint = f"provider rejected the request for {model!r}"
    return f"{hint}. (HTTP {status}: {detail})"


class OpenAILLM:
    """Implements the same `complete()` contract as AnthropicLLM, so callers
    can't tell which provider they're talking to."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Any | None = None,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
    ):
        if not model:
            raise ValueError(
                "no model selected for this provider — pick one in Settings "
                "(the list loads from the provider once a key is saved)."
            )
        # Accepts the endpoint URL people actually paste, not just the root.
        self.base_url = normalize_base_url(base_url) or "https://api.openai.com/v1"
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.context_window = context_window
        self._transport = transport  # injected in tests; None means real httpx

    # -- outbound translation ----------------------------------------------

    @staticmethod
    def _tools_to_openai(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]

    @staticmethod
    def _messages_to_openai(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})

        for msg in messages:
            role, content = msg.get("role"), msg.get("content")

            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue

            blocks = content or []
            if role == "assistant":
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                calls = [
                    {
                        "id": b["id"],
                        "type": "function",
                        "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
                    }
                    for b in blocks
                    if b.get("type") == "tool_use"
                ]
                entry: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    entry["tool_calls"] = calls
                out.append(entry)
                continue

            # A user turn carrying tool results becomes one `tool` message per
            # result — OpenAI has no notion of several results in one turn.
            leftover = []
            for b in blocks:
                if b.get("type") == "tool_result":
                    out.append({
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id", ""),
                        "content": b.get("content") if isinstance(b.get("content"), str) else json.dumps(b.get("content")),
                    })
                elif b.get("type") == "text":
                    leftover.append(b.get("text", ""))
            if leftover:
                out.append({"role": "user", "content": "\n".join(leftover)})

        return out

    # -- inbound translation -----------------------------------------------

    @staticmethod
    def _response_to_blocks(payload: dict[str, Any]) -> dict[str, Any]:
        choices = payload.get("choices") or []
        message = (choices[0] or {}).get("message", {}) if choices else {}

        blocks: list[dict[str, Any]] = []
        text = message.get("content")
        if text:
            blocks.append({"type": "text", "text": text})

        for call in message.get("tool_calls") or []:
            fn = call.get("function", {})
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except json.JSONDecodeError:
                # A model that emits malformed arguments should surface as a
                # tool error the loop can recover from, not a hard crash here.
                args = {"__malformed_arguments__": raw}
            blocks.append({
                "type": "tool_use",
                "id": call.get("id") or fn.get("name", "call"),
                "name": fn.get("name", ""),
                "input": args,
            })

        finish = (choices[0] or {}).get("finish_reason") if choices else None
        stop_reason = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else (
            "max_tokens" if finish == "length" else "end_turn"
        )
        return {"content": blocks, "stop_reason": stop_reason}

    # -- the call -----------------------------------------------------------

    def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages_to_openai(system, messages),
            "max_tokens": self.max_tokens,
        }
        converted = self._tools_to_openai(tools)
        if converted:
            body["tools"] = converted
            body["tool_choice"] = "auto"

        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if self._transport is not None:
            response = self._transport(url, headers, body)
        else:
            import httpx
            response = httpx.post(url, headers=headers, json=body, timeout=self.timeout)

        status = getattr(response, "status_code", 200)
        if status >= 400:
            detail = ""
            try:
                detail = json.dumps(response.json())[:400]
            except Exception:
                detail = getattr(response, "text", "")[:400]
            raise RuntimeError(explain_provider_error(status, detail, self.model, url))

        payload = response.json()
        result = self._response_to_blocks(payload)
        # OpenAI returns `usage` with `prompt_tokens`, `completion_tokens`,
        # and `total_tokens`. We map them to the same shape Anthropic
        # uses so the chat session doesn't care which provider is live.
        usage_obj = payload.get("usage") or {}
        # OpenAI-shaped providers cache automatically (no cache_control to
        # send) and report the hit under prompt_tokens_details.cached_tokens
        # when they support it. Forwarded under the Anthropic field name so
        # the chat panel has one shape to render. Note the semantics differ:
        # OpenAI's prompt_tokens *includes* the cached tokens, Anthropic's
        # input_tokens excludes them — which is why the panel adds the
        # fields up rather than reading input_tokens alone.
        details = usage_obj.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens") or 0)
        usage = {
            "input_tokens": max(int(usage_obj.get("prompt_tokens") or 0) - cached, 0),
            "output_tokens": int(usage_obj.get("completion_tokens") or 0),
            "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": 0,
        }
        result["usage"] = usage
        result["context_window"] = self.context_window
        return result
