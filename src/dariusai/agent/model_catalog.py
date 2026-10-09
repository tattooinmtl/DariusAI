"""Ask a provider what models it actually has, instead of making the user
type a model id from memory.

A hand-typed model name is a silent failure waiting to happen: it's only
wrong at the moment you finally send a real request, the error comes back
from the provider as a 404 with no suggestions, and nothing in the app can
tell you what you *should* have written. Every provider worth using exposes
a catalogue endpoint, so the model field is a list you pick from, populated
the moment a key is saved.

Free-only providers
-------------------
NVIDIA, OpenRouter, Agnes-AI and OpenCode are configured `free_only`: their
catalogues are filtered down to endpoints that cost nothing to call. "Free"
is established from the provider's own data, never assumed —

  * an id ending in `:free` (OpenRouter's convention), or
  * pricing fields that are all zero.

If a provider returns a catalogue with no pricing information at all, there
is nothing to filter on. Rather than show an empty list (which would look
like the fetch failed) the models come back with `free=None`, and the API
reports `free_filter: "unverifiable"` so the UI can say plainly that the
provider didn't disclose pricing.

Everything is driven off `base_url`, so a provider not in this table — or
one whose endpoint moved — still discovers correctly as long as it speaks
the near-universal OpenAI `/models` shape. The table is a convenience, not
a gate.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

REQUEST_TIMEOUT = 15.0


@dataclass(frozen=True)
class ModelSpec:
    """Published per-model numbers. Leave a field empty when the provider
    does not publish it — a guessed tokens-per-minute is worse than none."""

    id: str
    context_window: int | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ProviderSpec:
    base_url: str
    models_path: str = "/models"
    auth: str = "bearer"  # "bearer" | "x-api-key"
    free_only: bool = False
    extra_headers: dict[str, str] = field(default_factory=dict)
    label: str = ""
    # Known-good models to offer if the catalogue endpoint doesn't answer
    # (not every gateway exposes /models). Better a working default than a
    # provider that can't be used at all because discovery failed.
    models: tuple[ModelSpec, ...] = ()
    # The wire protocol, which is what actually decides the client. Only two
    # exist in practice: Anthropic's /v1/messages and OpenAI's
    # /chat/completions. Every gateway in this table but Anthropic is the
    # latter, which is why "OpenAI-compatible" is the default.
    protocol: str = "openai"
    # Agnes-AI publishes its catalogue as a document, not an endpoint —
    # there is no GET /models to call. Firing one off anyway just buys a
    # 404 and a confusing error, so it's skipped and the documented ids are
    # served directly.
    has_catalogue: bool = True
    # Published request cap for this preset's tier. None means the provider
    # does not publish one number (it varies by plan or by model). The tool
    # loop spaces calls to stay under it. Tokens-per-minute is not stored:
    # where a provider publishes it, the figure depends on the plan and a
    # single hardcoded value would be the wrong plan.
    rpm: int | None = None
    # Request budget when the provider publishes no separate max output.
    # MiniMax counts thinking tokens inside max_tokens, and 4096 comes back
    # empty. This is the size we ask for, not a published ceiling.
    completion_tokens: int | None = None
    # Override the client's wait. xKiro cuts a blocking call off at 95s.
    request_timeout: float | None = None

    @property
    def fallback_models(self) -> tuple[str, ...]:
        return tuple(m.id for m in self.models)

    def find(self, model_id: str) -> ModelSpec | None:
        for model in self.models:
            if model.id == model_id:
                return model
        return None


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        base_url="https://api.anthropic.com",
        models_path="/v1/models",
        auth="x-api-key",
        extra_headers={"anthropic-version": "2023-06-01"},
        label="Anthropic",
        protocol="anthropic",
    ),
    "openrouter": ProviderSpec(
        base_url="https://openrouter.ai/api/v1",
        free_only=True,
        label="OpenRouter (free only)",
        # `openrouter/free` is OpenRouter's own router that picks among the
        # free models for you — a safe default if discovery is unavailable.
        models=(ModelSpec("openrouter/free"),),
        # :free models, openrouter.ai/docs/api-reference/limits. 20/min
        # either way. The daily cap is 50 or 1,000 depending on whether the
        # account has bought 10 credits, so it is not paced here.
        rpm=20,
    ),
    "nvidia": ProviderSpec(
        base_url="https://integrate.api.nvidia.com/v1",
        free_only=True,
        label="NVIDIA NIM (free only)",
        # Published free-tier cap. NVIDIA says the live number moves with
        # the model and with other people's traffic. No TPM is published.
        rpm=40,
    ),
    # Agnes-AI and OpenCode both front OpenAI-shaped gateways. If either
    # moves, the provider still works — type the right base URL in Settings
    # and discovery follows it, because nothing here is hardcoded per
    # provider beyond the default.
    # Verified against platform.minimax.io/docs (Aug 2026): OpenAI-shaped
    # POST /v1/chat/completions, Bearer auth, and — like Agnes — no
    # GET /v1/models, so the documented ids are served directly rather than
    # buying a 404. api.minimaxi.com is the China-region host; the .io host
    # is the international one.
    "minimax": ProviderSpec(
        base_url="https://api.minimax.io/v1",
        label="MiniMax",
        has_catalogue=False,
        # platform.minimax.io API overview. The window is input + output
        # together. No tokens-per-minute figure; Token Plan quota is a
        # 5-hour window plus a weekly window. M3.1-Flash-Preview is Token
        # Plan / MiniMax Code only. completion_tokens is the size we ask
        # for: MiniMax counts thinking inside max_tokens, and 4096 returns
        # an empty answer. It is not a published output ceiling.
        completion_tokens=16_384,
        models=(
            ModelSpec("MiniMax-M3", context_window=1_000_000),
            ModelSpec("MiniMax-M3.1-Flash-Preview", context_window=1_000_000),
            ModelSpec("MiniMax-M2.7", context_window=204_800),
            ModelSpec("MiniMax-M2.7-highspeed", context_window=204_800),
            ModelSpec("MiniMax-M2.5", context_window=204_800),
            ModelSpec("MiniMax-M2.5-highspeed", context_window=204_800),
            ModelSpec("MiniMax-M2.1", context_window=204_800),
            ModelSpec("MiniMax-M2.1-highspeed", context_window=204_800),
            ModelSpec("MiniMax-M2", context_window=204_800),
        ),
    ),
    "agnes-ai": ProviderSpec(
        base_url="https://apihub.agnes-ai.com/v1",
        free_only=True,
        label="Agnes-AI (free only)",
        has_catalogue=False,  # documented model list, no /models endpoint
        # Agnes MODEL_CATALOG rev 2026.07.30. "512K" / "256K" / "64K" are
        # 1024-based; "65.5K" is 65,536 written in decimal thousands.
        # Text RPM on the free plan is 20 actual (enterprise 40, Token
        # Plan 1,000). This preset is the free plan, so the pace is 20.
        # Image and video models are not listed: this feeds the chat model.
        rpm=20,
        models=(
            ModelSpec("agnes-2.5-flash", context_window=524_288, max_output_tokens=65_536),
            ModelSpec("agnes-2.0-flash", context_window=262_144, max_output_tokens=65_536),
            ModelSpec("agnes-1.5-flash", context_window=262_144, max_output_tokens=65_536),
        ),
    ),
    "opencode": ProviderSpec(
        base_url="https://opencode.ai/zen/v1",
        free_only=True,
        label="OpenCode (free only)",
    ),
    # Groq publishes per-model context and max completion on its models
    # page. RPM/TPM differ between the free rate-limits table and the
    # Developer-plan column, so neither is hardcoded. The live /models
    # list wins when a key is saved; these ids are the fallback.
    "groq": ProviderSpec(
        base_url="https://api.groq.com/openai/v1",
        label="Groq",
        models=(
            ModelSpec("llama-3.3-70b-versatile", context_window=131_072, max_output_tokens=32_768),
            ModelSpec("llama-3.1-8b-instant", context_window=131_072, max_output_tokens=131_072),
            ModelSpec("openai/gpt-oss-120b", context_window=131_072, max_output_tokens=65_536),
            ModelSpec("openai/gpt-oss-20b", context_window=131_072, max_output_tokens=65_536),
            ModelSpec("qwen/qwen3.8-27b", context_window=131_072, max_output_tokens=16_384),
        ),
    ),
    # Public catalogue at GET /v1/models carries context_length,
    # max_output_tokens and tool support. Chat RPM is per plan and is not
    # one number. A blocking call is cut off at 95 seconds.
    "xkiro": ProviderSpec(
        base_url="https://api.xkiro.com/v1",
        label="xKiro",
        request_timeout=90.0,
    ),
}

GENERIC = ProviderSpec(base_url="", label="OpenAI-compatible")


def spec_for(name: str) -> ProviderSpec:
    return PROVIDER_SPECS.get((name or "").strip().lower(), GENERIC)


def presets() -> list[dict[str, Any]]:
    """What the Settings panel offers in its provider dropdown."""
    return [
        {"name": name, "label": s.label or name, "base_url": s.base_url,
         "free_only": s.free_only, "protocol": s.protocol, "rpm": s.rpm}
        for name, s in PROVIDER_SPECS.items()
    ]


# Filled when a catalogue (live or documented) is read, so a later
# build_llm can use context the static table does not know — NVIDIA and
# xKiro publish it per model on the wire, not in this file.
_catalogue_limits: dict[tuple[str, str], dict[str, Any]] = {}


def remember_models(name: str, models: list[dict[str, Any]]) -> None:
    key = (name or "").strip().lower()
    for model in models:
        model_id = model.get("id")
        if not model_id:
            continue
        _catalogue_limits[(key, str(model_id))] = {
            "context": model.get("context"),
            "max_output": model.get("max_output"),
        }


@dataclass(frozen=True)
class CallLimits:
    context_window: int | None = None
    max_output_tokens: int | None = None
    rpm: int | None = None
    timeout: float | None = None
    completion_tokens: int | None = None


def limits_for(name: str, model_id: str) -> CallLimits:
    """Context, output budget and request pace for one saved model.

    Static numbers win when we have them. A catalogue entry fills the
    gaps. Anything still empty stays empty so the caller can use its
    own default instead of a made-up figure.
    """
    spec = spec_for(name)
    found = spec.find(model_id)
    seen = _catalogue_limits.get(((name or "").strip().lower(), model_id or ""), {})
    context = (found.context_window if found else None) or seen.get("context")
    max_output = (found.max_output_tokens if found else None) or seen.get("max_output")
    return CallLimits(
        context_window=context,
        max_output_tokens=max_output,
        rpm=spec.rpm,
        timeout=spec.request_timeout,
        completion_tokens=spec.completion_tokens,
    )


class RequestPace:
    """Space calls so a 60-step tool turn does not spend a per-minute cap
    in the first second. One gate per provider name, shared by every
    client built for it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}

    def wait(self, key: str, rpm: int | None, sleep: Callable[[float], None], now: Callable[[], float]) -> None:
        if not rpm or rpm <= 0 or not key:
            return
        interval = 60.0 / rpm
        with self._lock:
            current = now()
            ready = self._next.get(key, current)
            delay = max(0.0, ready - current)
            self._next[key] = max(ready, current) + interval
        if delay > 0:
            sleep(delay)


PACE = RequestPace()


def _zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


_FREE_WORD = re.compile(r"(^|[\s\-_:/])free($|[\s\-_:/])", re.IGNORECASE)


def is_free(entry: dict[str, Any]) -> bool | None:
    """True/False when the provider says enough to know, None when it
    doesn't. The distinction matters: None means "unverifiable", and
    silently treating that as False would empty the list of a provider that
    is in fact free."""
    model_id = str(entry.get("id") or entry.get("name") or "")
    if model_id.endswith(":free"):
        return True
    # OpenCode Zen names its free tier in the model itself ("MiMo V2 Pro
    # Free", "Nemotron 3 Super Free") rather than via a suffix convention or
    # pricing fields. Word-boundary matched so "freeform" doesn't qualify.
    if _FREE_WORD.search(model_id) or _FREE_WORD.search(str(entry.get("display_name") or "")):
        return True

    pricing = entry.get("pricing")
    if isinstance(pricing, dict) and pricing:
        priced = [v for k, v in pricing.items() if k in ("prompt", "completion", "request", "input", "output")]
        if priced:
            return all(_zero(v) for v in priced)
    return None


def _normalize(entry: Any) -> dict[str, Any] | None:
    if isinstance(entry, str):
        return {"id": entry, "label": entry, "free": None, "context": None}
    if not isinstance(entry, dict):
        return None
    model_id = entry.get("id") or entry.get("name") or entry.get("model")
    if not model_id:
        return None
    return {
        "id": str(model_id),
        "label": str(entry.get("display_name") or entry.get("name") or model_id),
        "free": is_free(entry),
        "context": entry.get("context_length") or entry.get("context_window"),
        "max_output": entry.get("max_output_tokens") or entry.get("max_completion_tokens"),
    }


def parse_catalogue(payload: Any) -> list[dict[str, Any]]:
    """Accepts the shapes real providers actually return: OpenAI's
    {"data": [...]}, Anthropic's {"data": [...]} with display_name, and a
    bare list."""
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    out = []
    for row in rows:
        parsed = _normalize(row)
        if parsed:
            out.append(parsed)
    return out


def _headers(spec: ProviderSpec, api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json", **spec.extra_headers}
    if api_key:
        if spec.auth == "x-api-key":
            headers["x-api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    return headers


# Endpoint paths people paste into a "Base URL" field, because that's the URL
# every provider's quickstart puts in front of them. Appending /models to one
# of these produces .../chat/completions/models and a 404, so they're trimmed
# back to the API root first.
_ENDPOINT_SUFFIXES = (
    "/chat/completions",
    "/completions",
    "/responses",
    "/embeddings",
    "/messages",
)


def normalize_base_url(base_url: str) -> str:
    """Trim a pasted endpoint URL back to the API root it belongs to.

    Forgiving on purpose: 'https://host/v1/chat/completions' and
    'https://host/v1' mean the same provider, and a field that only accepts
    one spelling is a field that fails for the reason nobody can see.
    """
    root = (base_url or "").strip().rstrip("/")
    changed = True
    while changed:
        changed = False
        for suffix in _ENDPOINT_SUFFIXES:
            if root.lower().endswith(suffix):
                root = root[: -len(suffix)].rstrip("/")
                changed = True
    return root


def catalogue_url(name: str, base_url: str = "") -> str:
    spec = spec_for(name)
    root = normalize_base_url(base_url) or spec.base_url.rstrip("/")
    if not root:
        raise ValueError("no base URL for this provider — set one in Settings")
    path = spec.models_path
    # A base URL that already ends in /v1 shouldn't become /v1/v1/models.
    if root.endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    return root + path


def fallback_result(name: str, reason: str = "") -> dict[str, Any]:
    """The documented model ids for a provider whose catalogue can't be
    reached — so a provider is never a dead end just because discovery is
    unavailable. Empty `models` when nothing is known, which the UI reports
    honestly rather than pretending to have a list."""
    spec = spec_for(name)
    models = [
        {
            "id": model.id,
            "label": model.id,
            "free": True if spec.free_only else None,
            "context": model.context_window,
            "max_output": model.max_output_tokens,
        }
        for model in spec.models
    ]
    remember_models(name, models)
    return {
        "models": models,
        "free_only": spec.free_only,
        "free_filter": "off",
        "total": len(models),
        "url": "",
        "source": "documented",
        "note": reason,
        "rpm": spec.rpm,
    }


def fetch_models(
    name: str,
    base_url: str = "",
    api_key: str = "",
    http_get: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Returns {"models": [...], "free_only": bool, "free_filter": str,
    "total": int, "url": str}. `http_get` is injectable so the whole thing
    is testable without touching the network."""
    spec = spec_for(name)

    if not spec.has_catalogue:
        return fallback_result(name, reason="this provider publishes its models as documentation, not an endpoint")

    url = catalogue_url(name, base_url)

    if http_get is None:
        import httpx

        def http_get(u, headers):  # noqa: ANN001 - local shim
            return httpx.get(u, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True)

    response = http_get(url, _headers(spec, api_key))
    status = getattr(response, "status_code", 200)
    if status >= 400:
        raise RuntimeError(f"{name or 'provider'} returned HTTP {status} for {url}")

    models = parse_catalogue(response.json())
    total = len(models)
    free_filter = "off"

    if spec.free_only:
        provably_free = [m for m in models if m["free"] is True]
        if provably_free:
            models, free_filter = provably_free, "applied"
        elif any(m["free"] is False for m in models):
            models, free_filter = [], "applied"  # priced catalogue, nothing free in it
        else:
            free_filter = "unverifiable"  # no pricing disclosed — show all, flagged

    models.sort(key=lambda m: m["id"])
    remember_models(name, models)
    return {
        "models": models,
        "free_only": spec.free_only,
        "free_filter": free_filter,
        "total": total,
        "url": url,
        "source": "live",
        "note": "",
        "rpm": spec.rpm,
    }
