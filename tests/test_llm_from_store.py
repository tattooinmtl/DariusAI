import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent.llm import AnthropicLLM
from dariusai.brain.store import BrainStore


def test_from_store_uses_active_provider(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", base_url="https://api.anthropic.com", model="claude-opus-5", api_key="sk-ant-fromstore")
    store.set_active_provider("anthropic")

    llm = AnthropicLLM.from_store(store)
    assert llm.model == "claude-opus-5"
    assert llm.client.api_key == "sk-ant-fromstore"


def test_from_store_falls_back_to_default_model_when_provider_model_blank(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", api_key="sk-ant-x")  # no model specified
    store.set_active_provider("anthropic")

    llm = AnthropicLLM.from_store(store)
    from dariusai.agent.llm import DEFAULT_MODEL
    assert llm.model == DEFAULT_MODEL


def test_from_store_explicit_model_overrides_provider_model(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", model="claude-opus-5", api_key="sk-ant-x")
    store.set_active_provider("anthropic")

    llm = AnthropicLLM.from_store(store, model="claude-haiku-4-5-20251001")
    assert llm.model == "claude-haiku-4-5-20251001"


def test_from_store_no_active_provider_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv")
    store = BrainStore(tmp_path)  # no providers configured at all

    llm = AnthropicLLM.from_store(store)
    assert llm.client.api_key == "sk-ant-fromenv"


def test_from_store_provider_without_key_falls_back_to_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fromenv")
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", base_url="https://api.anthropic.com")  # active but no key
    store.set_active_provider("anthropic")

    llm = AnthropicLLM.from_store(store)
    assert llm.client.api_key == "sk-ant-fromenv"
