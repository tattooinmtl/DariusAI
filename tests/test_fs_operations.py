"""The file-explorer operations — the app's first destructive file API.

A traversal bug here deletes real work, so the escape attempts are tested
first and in every direction: source paths, destination paths, and the
folder-into-itself cases that `..` checking alone doesn't catch.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.viz.server import create_app


@pytest.fixture()
def project(tmp_path):
    """A project with a top-level folder, a nested file, and a sibling
    directory *outside* the project that nothing may reach."""
    root = tmp_path / "project"
    (root / "src" / "deep").mkdir(parents=True)
    (root / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    (root / "src" / "deep" / "nested.txt").write_text("nested", encoding="utf-8")
    (root / "notes.md").write_text("# notes", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not touch", encoding="utf-8")
    return root, outside


@pytest.fixture()
def client(project, tmp_path):
    root, _ = project
    return TestClient(create_app(tmp_path / "brain", project_dir=root))


def delete(client, **body):
    return client.request("DELETE", "/api/fs", json=body)


# ---- escapes --------------------------------------------------------------

@pytest.mark.parametrize("escape", ["../outside/secret.txt", "src/../../outside/secret.txt", "../outside"])
def test_delete_cannot_escape_the_project(client, project, escape):
    _, outside = project
    r = delete(client, path=escape, recursive=True, allow_top_level=True)
    assert r.status_code == 400
    assert (outside / "secret.txt").exists(), "a file outside the project was deleted"


@pytest.mark.parametrize("endpoint", ["/api/fs/rename", "/api/fs/copy"])
def test_destination_cannot_escape_the_project(client, project, endpoint):
    """The hole an unchecked destination opens: the source is legal, the
    target is anywhere on disk."""
    _, outside = project
    r = client.post(endpoint, json={"path": "notes.md", "dest": "../outside/stolen.md"})
    assert r.status_code == 400
    assert not (outside / "stolen.md").exists()


@pytest.mark.parametrize("endpoint", ["/api/fs/mkdir", "/api/fs/new-file"])
def test_creation_cannot_escape_the_project(client, project, endpoint):
    _, outside = project
    r = client.post(endpoint, json={"path": "", "name": "../outside/injected"})
    assert r.status_code == 400
    assert not (outside / "injected").exists()


# ---- the deletion guards --------------------------------------------------

def test_the_project_root_can_never_be_deleted(client, project):
    root, _ = project
    r = delete(client, path="", recursive=True, allow_top_level=True)
    assert r.status_code == 400
    assert "project root" in r.json()["detail"]
    assert root.exists()


def test_a_top_level_folder_needs_explicit_confirmation(client, project):
    """src/ and tests/ are the structural ones — a mis-click there is the
    expensive kind, so it takes more than the recursive flag."""
    root, _ = project
    r = delete(client, path="src", recursive=True)          # no allow_top_level
    assert r.status_code == 409
    assert "top-level" in r.json()["detail"]
    assert (root / "src").exists()

    r = delete(client, path="src", recursive=True, allow_top_level=True)
    assert r.status_code == 200
    assert not (root / "src").exists()


def test_a_nested_non_empty_folder_needs_the_recursive_flag(client, project):
    root, _ = project
    r = delete(client, path="src/deep")
    assert r.status_code == 409
    assert "not empty" in r.json()["detail"]
    assert (root / "src" / "deep").exists()

    assert delete(client, path="src/deep", recursive=True).status_code == 200
    assert not (root / "src" / "deep").exists()


def test_deleting_a_plain_file_needs_no_flags(client, project):
    root, _ = project
    assert delete(client, path="notes.md").status_code == 200
    assert not (root / "notes.md").exists()


def test_deleting_something_that_is_not_there_is_a_404(client):
    assert delete(client, path="ghost.txt").status_code == 404


# ---- rename / move --------------------------------------------------------

def test_rename_within_a_folder(client, project):
    root, _ = project
    r = client.post("/api/fs/rename", json={"path": "notes.md", "dest": "README.md"})
    assert r.status_code == 200
    assert (root / "README.md").exists() and not (root / "notes.md").exists()


def test_move_into_another_folder(client, project):
    root, _ = project
    r = client.post("/api/fs/rename", json={"path": "notes.md", "dest": "src/notes.md"})
    assert r.status_code == 200
    assert (root / "src" / "notes.md").exists()


def test_move_refuses_to_clobber_without_overwrite(client, project):
    root, _ = project
    r = client.post("/api/fs/rename", json={"path": "notes.md", "dest": "src/main.py"})
    assert r.status_code == 409
    assert (root / "src" / "main.py").read_text(encoding="utf-8") == "print('hi')"


def test_a_folder_cannot_be_moved_inside_itself(client, project):
    r = client.post("/api/fs/rename", json={"path": "src", "dest": "src/deep/src"})
    assert r.status_code == 400


# ---- copy -----------------------------------------------------------------

def test_copy_a_file(client, project):
    root, _ = project
    r = client.post("/api/fs/copy", json={"path": "notes.md", "dest": "notes-backup.md"})
    assert r.status_code == 200
    assert (root / "notes-backup.md").read_text(encoding="utf-8") == "# notes"
    assert (root / "notes.md").exists(), "copy must not remove the original"


def test_copy_a_whole_folder(client, project):
    root, _ = project
    r = client.post("/api/fs/copy", json={"path": "src", "dest": "src-copy"})
    assert r.status_code == 200
    assert (root / "src-copy" / "deep" / "nested.txt").read_text(encoding="utf-8") == "nested"


# ---- create ---------------------------------------------------------------

def test_new_folder_and_new_file(client, project):
    root, _ = project
    assert client.post("/api/fs/mkdir", json={"path": "", "name": "docs"}).status_code == 200
    assert (root / "docs").is_dir()

    assert client.post("/api/fs/new-file", json={"path": "docs", "name": "index.md"}).status_code == 200
    assert (root / "docs" / "index.md").read_text(encoding="utf-8") == ""


def test_creating_over_something_existing_is_refused(client, project):
    assert client.post("/api/fs/mkdir", json={"path": "", "name": "src"}).status_code == 409
    assert client.post("/api/fs/new-file", json={"path": "", "name": "notes.md"}).status_code == 409
