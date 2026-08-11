"""Model discovery, including the free-only filter for NVIDIA, OpenRouter,
Agnes-AI and OpenCode. All offline — the HTTP call is injected."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent import model_catalog as mc
from dariusai.viz.server import create_app


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def getter(payload, status=200, captured=None):
    def _get(url, headers):
        if captured is not None:
            captured["url"] = url
            captured["headers"] = headers
        return FakeResponse(payload, status)
    return _get


def test_openai_shape_is_parsed():
    got = mc.fetch_models("custom", base_url="https://x.test/v1", api_key="k",
                          http_get=getter({"data": [{"id": "gpt-x"}, {"id": "gpt-y"}]}))
    assert [m["id"] for m in got["models"]] == ["gpt-x", "gpt-y"]
    assert got["free_only"] is False


def test_anthropic_uses_its_own_auth_header_and_version():
    captured = {}
    mc.fetch_models("anthropic", api_key="sk-ant", http_get=getter({"data": []}, captured=captured))
    assert captured["headers"]["x-api-key"] == "sk-ant"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in captured["headers"]
    assert captured["url"] == "https://api.anthropic.com/v1/models"


def test_bearer_auth_for_openai_style_providers():
    captured = {}
    mc.fetch_models("openrouter", api_key="key123", http_get=getter({"data": []}, captured=captured))
    assert captured["headers"]["Authorization"] == "Bearer key123"


@pytest.mark.parametrize("name", ["openrouter", "nvidia", "agnes-ai", "opencode"])
def test_the_four_named_providers_are_free_only(name):
    assert mc.spec_for(name).free_only is True


def test_free_filter_keeps_only_zero_priced_models():
    payload = {"data": [
        {"id": "meta/llama-3-70b:free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "openai/gpt-4o", "pricing": {"prompt": "0.000005", "completion": "0.000015"}},
        {"id": "mistral/mixtral", "pricing": {"prompt": "0", "completion": "0"}},
    ]}
    got = mc.fetch_models("openrouter", api_key="k", http_get=getter(payload))
    assert [m["id"] for m in got["models"]] == ["meta/llama-3-70b:free", "mistral/mixtral"]
    assert got["free_filter"] == "applied"
    assert got["total"] == 3
    assert all(m["free"] is True for m in got["models"])


def test_free_suffix_alone_is_enough():
    got = mc.fetch_models("openrouter", api_key="k",
                          http_get=getter({"data": [{"id": "z/model:free"}, {"id": "z/model"}]}))
    assert [m["id"] for m in got["models"]] == ["z/model:free"]


def test_catalogue_without_pricing_is_reported_unverifiable_not_silently_emptied():
    """A provider that publishes no pricing must not come back as an empty
    list — that reads as a broken fetch. Show them, flagged."""
    got = mc.fetch_models("nvidia", api_key="k",
                          http_get=getter({"data": [{"id": "nv/llama"}, {"id": "nv/nemotron"}]}))
    assert got["free_filter"] == "unverifiable"
    assert len(got["models"]) == 2
    assert all(m["free"] is None for m in got["models"])


def test_priced_catalogue_with_nothing_free_returns_empty_applied():
    got = mc.fetch_models("nvidia", api_key="k",
                          http_get=getter({"data": [{"id": "a", "pricing": {"prompt": "0.01", "completion": "0.02"}}]}))
    assert got["models"] == []
    assert got["free_filter"] == "applied"


def test_base_url_ending_in_v1_does_not_double_up():
    assert mc.catalogue_url("anthropic", "https://gateway.test/v1") == "https://gateway.test/v1/models"


def test_http_error_is_raised_not_swallowed():
    with pytest.raises(RuntimeError, match="HTTP 401"):
        mc.fetch_models("openrouter", api_key="bad", http_get=getter({}, status=401))


def test_missing_base_url_is_a_clear_error():
    with pytest.raises(ValueError, match="no base URL"):
        mc.fetch_models("totally-unknown", api_key="k", http_get=getter({"data": []}))


def test_models_endpoint_uses_the_saved_key(tmp_path, monkeypatch):
    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    client = TestClient(app)
    client.put("/api/providers/openrouter", json={"base_url": "", "model": "", "api_key": "sk-saved"})

    seen = {}

    def fake_fetch(name, base_url="", api_key="", http_get=None):
        seen["name"], seen["api_key"] = name, api_key
        return {"models": [{"id": "m/free", "label": "m/free", "free": True, "context": None}],
                "free_only": True, "free_filter": "applied", "total": 5, "url": "u"}

    monkeypatch.setattr(mc, "fetch_models", fake_fetch)
    body = client.get("/api/providers/openrouter/models").json()

    assert seen["api_key"] == "sk-saved"  # page never has to hold the key
    assert body["models"][0]["id"] == "m/free"
    assert body["free_filter"] == "applied"


def test_models_endpoint_404s_for_an_unknown_provider(tmp_path):
    client = TestClient(create_app(tmp_path / "brain", project_dir=tmp_path))
    assert client.get("/api/providers/nope/models").status_code == 404


def test_presets_expose_the_free_only_providers(tmp_path):
    client = TestClient(create_app(tmp_path / "brain", project_dir=tmp_path))
    names = {p["name"]: p for p in client.get("/api/provider-presets").json()}
    for required in ("nvidia", "openrouter", "agnes-ai", "opencode"):
        assert names[required]["free_only"] is True
        assert names[required]["base_url"].startswith("https://")


# ---- verified against the providers' own docs (Aug 2026) -------------------

def test_agnes_uses_the_apihub_base_url_and_documented_chat_models():
    spec = mc.spec_for("agnes-ai")
    assert spec.base_url == "https://apihub.agnes-ai.com/v1"
    assert spec.auth == "bearer"
    # Agnes documents its catalogue instead of serving GET /models.
    assert spec.has_catalogue is False
    assert spec.fallback_models == ("agnes-2.5-flash", "agnes-2.0-flash")


def test_agnes_returns_documented_models_without_any_http_call():
    def explode(url, headers):
        raise AssertionError("must not call a /models endpoint Agnes does not have")

    got = mc.fetch_models("agnes-ai", api_key="k", http_get=explode)
    assert [m["id"] for m in got["models"]] == ["agnes-2.5-flash", "agnes-2.0-flash"]
    assert got["source"] == "documented"


def test_opencode_free_models_are_detected_by_name():
    """OpenCode Zen marks its free tier in the model name rather than with
    a :free suffix or pricing fields."""
    payload = {"data": [
        {"id": "mimo-v2-pro-free"},
        {"id": "minimax-m2.5-free"},
        {"id": "big-pickle"},
    ]}
    got = mc.fetch_models("opencode", api_key="k", http_get=getter(payload))
    assert [m["id"] for m in got["models"]] == ["mimo-v2-pro-free", "minimax-m2.5-free"]
    assert got["free_filter"] == "applied"


def test_free_word_match_is_word_bounded():
    assert mc.is_free({"id": "vendor/freeform-writer"}) is None
    assert mc.is_free({"id": "vendor/model-free"}) is True


def test_openrouter_falls_back_to_its_free_router():
    assert mc.spec_for("openrouter").fallback_models == ("openrouter/free",)


def test_endpoint_falls_back_instead_of_502_when_the_catalogue_is_unreachable(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path / "brain", project_dir=tmp_path))
    client.put("/api/providers/openrouter", json={"base_url": "", "model": "", "api_key": "sk"})

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(mc, "fetch_models", boom)
    r = client.get("/api/providers/openrouter/models")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "documented"
    assert body["models"][0]["id"] == "openrouter/free"
    assert "network down" in body["note"]


# ---- pasted endpoint URLs (the real-world failure) ------------------------

@pytest.mark.parametrize("pasted", [
    "https://integrate.api.nvidia.com/v1/chat/completions",
    "https://integrate.api.nvidia.com/v1/chat/completions/",
    "https://integrate.api.nvidia.com/v1/completions",
    "https://integrate.api.nvidia.com/v1",
    "https://integrate.api.nvidia.com/v1/",
])
def test_a_pasted_endpoint_url_still_finds_the_catalogue(pasted):
    """Every provider's quickstart shows the chat-completions URL, so that's
    what lands in a "Base URL" field. Appending /models to it gave
    .../chat/completions/models and a 404."""
    assert mc.catalogue_url("nvidia", pasted) == "https://integrate.api.nvidia.com/v1/models"


def test_anthropic_messages_endpoint_is_trimmed_too():
    assert mc.catalogue_url("anthropic", "https://api.anthropic.com/v1/messages") == \
        "https://api.anthropic.com/v1/models"


def test_normalize_leaves_a_plain_root_alone():
    assert mc.normalize_base_url("https://host.test/v1") == "https://host.test/v1"
    assert mc.normalize_base_url("") == ""


# ---- MiniMax (verified against platform.minimax.io docs, Aug 2026) ---------

def test_minimax_preset_matches_the_documented_api():
    spec = mc.spec_for("minimax")
    assert spec.base_url == "https://api.minimax.io/v1"
    assert spec.auth == "bearer"
    assert spec.protocol == "openai"
    assert spec.has_catalogue is False       # no GET /v1/models exists
    assert spec.fallback_models[0] == "MiniMax-M3"


def test_minimax_serves_documented_models_without_an_http_call():
    def explode(url, headers):
        raise AssertionError("MiniMax has no /models endpoint to call")
    got = mc.fetch_models("minimax", api_key="k", http_get=explode)
    assert "MiniMax-M3" in [m["id"] for m in got["models"]]
    assert got["source"] == "documented"


# ---- provider routing: the fix for the 404 --------------------------------

def _store_with(tmp_path, name, base_url, model):
    from dariusai.brain.store import BrainStore
    store = BrainStore(tmp_path / "brain")
    store.upsert_provider(name, base_url=base_url, model=model, api_key="test-key")
    store.set_active_provider(name)
    return store


@pytest.mark.parametrize("name,base,expected", [
    ("minimax", "https://api.minimax.io/v1", "OpenAILLM"),
    ("nvidia", "https://integrate.api.nvidia.com/v1", "OpenAILLM"),
    ("openrouter", "https://openrouter.ai/api/v1", "OpenAILLM"),
    ("agnes-ai", "https://apihub.agnes-ai.com/v1", "OpenAILLM"),
])
def test_openai_shaped_providers_get_the_openai_client(tmp_path, name, base, expected):
    """The routing whose absence produced the 404: an Anthropic client
    posting /v1/messages to a provider that only serves /chat/completions."""
    from dariusai.agent.llm import build_llm
    llm = build_llm(_store_with(tmp_path, name, base, "some-model"))
    assert type(llm).__name__ == expected


def test_a_pasted_endpoint_url_still_routes_to_the_right_place(tmp_path):
    """Pasting the full chat-completions URL is the common mistake; it must
    not produce a doubled path."""
    from dariusai.agent.llm import build_llm
    llm = build_llm(_store_with(
        tmp_path, "minimax", "https://api.minimax.io/v1/chat/completions", "MiniMax-M3"))
    assert llm.base_url == "https://api.minimax.io/v1"
    assert llm.model == "MiniMax-M3"


def test_an_empty_base_url_falls_back_to_the_preset_not_openai(tmp_path):
    """An empty string would normalise to api.openai.com — i.e. a MiniMax
    key sent to OpenAI."""
    from dariusai.agent.llm import build_llm
    llm = build_llm(_store_with(tmp_path, "minimax", "", "MiniMax-M3"))
    assert llm.base_url == "https://api.minimax.io/v1"
