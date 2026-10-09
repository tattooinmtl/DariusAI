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
    # create_app records the project it was opened on (the editor and the
    # sandbox both read it back), so it sits beside the key we wrote.
    assert r2.json() == {"theme": "dark", "project_dir": str(tmp_path)}


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


class _LiveChat:
    """Stand-in for an open ChatSession. Only `.llm` is what Settings swaps."""

    def __init__(self, llm):
        self.llm = llm


def test_activate_swaps_the_open_chat_client(tmp_path):
    """Use this must change the client the open chat will call next.
    Writing the database alone leaves the panel on the previous key."""
    _, client = make_client(tmp_path)
    app = client.app
    stale = _LiveChat(llm="stale")
    app.state.chat_sessions.append(stale)

    client.put("/api/providers/minimax", json={
        "base_url": "https://api.minimax.io/v1",
        "model": "MiniMax-M3",
        "api_key": "sk-cp-live",
    })
    r = client.put("/api/providers/minimax/activate")
    assert r.status_code == 200

    assert stale.llm is app.state.llm
    assert stale.llm.api_key == "sk-cp-live"
    assert stale.llm.model == "MiniMax-M3"
    assert stale.llm.base_url == "https://api.minimax.io/v1"


def test_saving_the_active_provider_swaps_the_open_chat_client(tmp_path):
    _, client = make_client(tmp_path)
    app = client.app
    stale = _LiveChat(llm="stale")
    app.state.chat_sessions.append(stale)

    client.put("/api/providers/nvidia", json={
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "old-model",
        "api_key": "nv-old",
    })
    client.put("/api/providers/nvidia/activate")
    previous = stale.llm

    r = client.put("/api/providers/nvidia", json={
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "z-ai/glm-5.3-flash",
        "api_key": "nv-new",
    })
    assert r.status_code == 200
    assert stale.llm is not previous
    assert stale.llm.api_key == "nv-new"
    assert stale.llm.model == "z-ai/glm-5.3-flash"


def test_saving_an_inactive_provider_leaves_the_open_chat_client(tmp_path):
    _, client = make_client(tmp_path)
    app = client.app
    stale = _LiveChat(llm="stale")
    app.state.chat_sessions.append(stale)

    client.put("/api/providers/minimax", json={
        "base_url": "https://api.minimax.io/v1",
        "model": "MiniMax-M3",
        "api_key": "sk-cp-live",
    })
    client.put("/api/providers/minimax/activate")
    current = stale.llm

    r = client.put("/api/providers/nvidia", json={
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": "z-ai/glm-5.3-flash",
        "api_key": "nv-other",
    })
    assert r.status_code == 200
    assert stale.llm is current
