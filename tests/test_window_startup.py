import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.viz.server import create_app
from dariusai.viz.window import _start_server


def test_start_server_binds_and_becomes_ready(tmp_path):
    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    server, port = _start_server(app, "127.0.0.1", 18765)
    try:
        assert server.started is True
        assert port == 18765
        import httpx
        r = httpx.get(f"http://127.0.0.1:{port}/api/graph", timeout=5)
        assert r.status_code == 200
    finally:
        server.should_exit = True


def test_start_server_falls_back_to_next_free_port(tmp_path):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 18770))
    blocker.listen(1)
    try:
        app = create_app(tmp_path / "brain", project_dir=tmp_path)
        server, port = _start_server(app, "127.0.0.1", 18770)
        try:
            assert port == 18771  # 18770 was taken, so it moved to the next one
            assert server.started is True
        finally:
            server.should_exit = True
    finally:
        blocker.close()
