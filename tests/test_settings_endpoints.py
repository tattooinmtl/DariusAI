import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from dariusai.viz.server import create_app


def make_client(tmp_path):
    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    return app.state.store, TestClient(app)


def test_settings_endpoint_roundtrip(tmp_path):
    _, client = make_client(tmp_path)
    r = client.put("/api/settings", json={"key": "theme", "value": "dark"})
    assert r.status_code == 200
    r2 = client.get("/api/settings")
    assert r2.json() == {"theme": "dark"}


def test_provider_endpoints_never_leak_plaintext_key(tmp_path):
    store, client = make_client(tmp_path)
    r = client.put("/api/providers/anthropic", json={"base_url": "https://api.anthropic.com", "model": "claude-sonnet-5", "api_key": "sk-ant-supersecret"})
    assert r.status_code == 200
    body = r.json()
    assert "supersecret" not in str(body)
    assert body["has_api_key"] is True

    r2 = client.get("/api/providers")
    assert "supersecret" not in str(r2.json())

    # but the real key is retrievable server-side for actually building a client
    assert store.get_provider_api_key("anthropic") == "sk-ant-supersecret"


def test_provider_update_without_key_keeps_it(tmp_path):
    store, client = make_client(tmp_path)
    client.put("/api/providers/anthropic", json={"api_key": "sk-original"})
    client.put("/api/providers/anthropic", json={"base_url": "https://new.example.com"})
    assert store.get_provider_api_key("anthropic") == "sk-original"


def test_activate_and_delete_provider(tmp_path):
    store, client = make_client(tmp_path)
    client.put("/api/providers/anthropic", json={"api_key": "k1"})
    client.put("/api/providers/openai_compatible", json={"api_key": "k2"})

    r = client.put("/api/providers/anthropic/activate")
    assert r.status_code == 200
    assert r.json()["is_active"] is True

    r2 = client.delete("/api/providers/openai_compatible")
    assert r2.status_code == 200
    assert store.get_provider("openai_compatible") is None


def test_activate_unknown_provider_404(tmp_path):
    _, client = make_client(tmp_path)
    r = client.put("/api/providers/does-not-exist/activate")
    assert r.status_code == 404
