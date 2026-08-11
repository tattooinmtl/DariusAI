import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from dariusai.viz.server import create_app


def make_client(tmp_path):
    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    return TestClient(app)


def test_health_ok(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
