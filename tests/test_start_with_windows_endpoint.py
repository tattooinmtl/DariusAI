import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from dariusai.viz.server import create_app


def test_start_with_windows_endpoints_never_touch_real_registry(tmp_path, monkeypatch):
    # Mocked at the os_integration module level — proves the endpoint wires
    # through correctly without this test suite ever writing to the real
    # Run key (that's covered separately, against a scratch key, in
    # test_os_integration.py).
    import dariusai.os_integration as osi

    state = {"enabled": False}
    monkeypatch.setattr(osi, "is_enabled", lambda *a, **k: state["enabled"])

    def fake_set_enabled(enabled, *a, **k):
        state["enabled"] = enabled
        return enabled
    monkeypatch.setattr(osi, "set_enabled", fake_set_enabled)

    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    client = TestClient(app)

    r = client.get("/api/start-with-windows")
    assert r.json() == {"enabled": False}

    r2 = client.put("/api/start-with-windows", json={"enabled": True})
    assert r2.json() == {"enabled": True}

    r3 = client.get("/api/start-with-windows")
    assert r3.json() == {"enabled": True}
