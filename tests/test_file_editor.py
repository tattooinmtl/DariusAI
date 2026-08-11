import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from dariusai.viz.server import create_app


def make_client(tmp_path, project_dir):
    app = create_app(tmp_path / "brain", project_dir=project_dir)
    return TestClient(app)


def test_list_files_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('hi')")
    (project / "src").mkdir()
    client = make_client(tmp_path, project)

    r = client.get("/api/files")
    assert r.status_code == 200
    names = {e["name"] for e in r.json()["entries"]}
    assert names == {"main.py", "src"}
    src_entry = next(e for e in r.json()["entries"] if e["name"] == "src")
    assert src_entry["is_dir"] is True


def test_list_files_subdirectory(tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("x = 1")
    client = make_client(tmp_path, project)

    r = client.get("/api/files", params={"path": "src"})
    assert r.status_code == 200
    assert r.json()["entries"][0]["name"] == "app.py"
    assert r.json()["entries"][0]["path"] == "src/app.py"


def test_read_file(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "readme.txt").write_text("hello world")
    client = make_client(tmp_path, project)

    r = client.get("/api/file", params={"path": "readme.txt"})
    assert r.status_code == 200
    assert r.json()["content"] == "hello world"


def test_write_then_read_back(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    client = make_client(tmp_path, project)

    r = client.put("/api/file", json={"path": "new_file.py", "content": "print(42)"})
    assert r.status_code == 200
    assert r.json()["status"] == "saved"
    assert (project / "new_file.py").read_text() == "print(42)"

    r2 = client.get("/api/file", params={"path": "new_file.py"})
    assert r2.json()["content"] == "print(42)"


def test_write_creates_parent_dirs(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    client = make_client(tmp_path, project)
    r = client.put("/api/file", json={"path": "nested/dir/file.txt", "content": "x"})
    assert r.status_code == 200
    assert (project / "nested" / "dir" / "file.txt").read_text() == "x"


def test_path_traversal_rejected(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "secret.txt").write_text("outside the project")
    client = make_client(tmp_path, project)

    r = client.get("/api/file", params={"path": "../secret.txt"})
    assert r.status_code == 400

    r2 = client.put("/api/file", json={"path": "../../evil.txt", "content": "pwned"})
    assert r2.status_code == 400
    assert not (tmp_path / "evil.txt").exists()


def test_read_missing_file_404(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    client = make_client(tmp_path, project)
    r = client.get("/api/file", params={"path": "nope.txt"})
    assert r.status_code == 404
