import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.brain.store import BrainStore


def test_settings_set_get_roundtrip(tmp_path):
    store = BrainStore(tmp_path)
    store.set_setting("last_project_dir", "C:\\Users\\erik\\project")
    assert store.get_setting("last_project_dir") == "C:\\Users\\erik\\project"
    assert store.get_setting("missing_key", "fallback") == "fallback"


def test_settings_overwrite(tmp_path):
    store = BrainStore(tmp_path)
    store.set_setting("theme", "dark")
    store.set_setting("theme", "light")
    assert store.get_setting("theme") == "light"


def test_all_settings_returns_dict(tmp_path):
    store = BrainStore(tmp_path)
    store.set_setting("a", "1")
    store.set_setting("b", "2")
    assert store.all_settings() == {"a": "1", "b": "2"}


def test_upsert_provider_creates_and_encrypts_key(tmp_path):
    store = BrainStore(tmp_path)
    result = store.upsert_provider("anthropic", base_url="https://api.anthropic.com", model="claude-sonnet-5", api_key="sk-ant-secret123")
    assert result["name"] == "anthropic"
    assert result["has_api_key"] is True
    assert "secret123" not in str(result)  # masked in the returned dict, not the raw key
    assert result["api_key_masked"].endswith("t123")

    # the raw ciphertext in the DB is never the plaintext key
    row = store.conn.execute("SELECT api_key_encrypted FROM providers WHERE name = 'anthropic'").fetchone()
    assert b"sk-ant-secret123" not in row[0]


def test_get_provider_api_key_decrypts_correctly(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", api_key="sk-ant-realkey")
    assert store.get_provider_api_key("anthropic") == "sk-ant-realkey"


def test_get_provider_api_key_missing_raises(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", base_url="x")  # no api_key at all
    try:
        store.get_provider_api_key("anthropic")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_upsert_provider_without_key_preserves_existing_key(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", api_key="sk-ant-original")
    store.upsert_provider("anthropic", base_url="https://new-url.example.com")  # api_key=None
    assert store.get_provider_api_key("anthropic") == "sk-ant-original"
    assert store.get_provider("anthropic")["base_url"] == "https://new-url.example.com"


def test_upsert_provider_empty_string_clears_key(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", api_key="sk-ant-original")
    store.upsert_provider("anthropic", api_key="")
    assert store.get_provider("anthropic")["has_api_key"] is False


def test_list_providers(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", api_key="key1")
    store.upsert_provider("openai_compatible", api_key="key2")
    names = {p["name"] for p in store.list_providers()}
    assert names == {"anthropic", "openai_compatible"}


def test_set_active_provider(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", api_key="k1")
    store.upsert_provider("openai_compatible", api_key="k2")
    store.set_active_provider("anthropic")
    active = store.get_active_provider()
    assert active["name"] == "anthropic"
    assert active["is_active"] is True

    store.set_active_provider("openai_compatible")
    active2 = store.get_active_provider()
    assert active2["name"] == "openai_compatible"
    # only one provider active at a time
    assert store.get_provider("anthropic")["is_active"] is False


def test_set_active_provider_unknown_raises(tmp_path):
    store = BrainStore(tmp_path)
    try:
        store.set_active_provider("does-not-exist")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_delete_provider(tmp_path):
    store = BrainStore(tmp_path)
    store.upsert_provider("anthropic", api_key="k1")
    store.delete_provider("anthropic")
    assert store.get_provider("anthropic") is None
