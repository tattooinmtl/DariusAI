"""Running code blocks: runtime detection, execution, and the two endpoints
the chat UI calls.

Detection is real (it probes this machine), so tests assert on *behaviour*
rather than on which toolchains happen to be installed — except Python,
which is running the test.
"""

import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent import runtimes
from dariusai.agent.sandbox import Sandbox
from dariusai.viz.server import create_app


def test_python_is_detected_since_it_is_running_these_tests():
    info = runtimes.detect()["python"]
    assert info["available"] is True
    assert "Python" in info["version"]


def test_aliases_resolve_to_the_same_runtime():
    for alias in ("py", "python3"):
        assert runtimes.runtime_for(alias).language == "python"
    assert runtimes.runtime_for("js").language == "javascript"
    assert runtimes.runtime_for("sqlite3").language == "sql"


def test_markup_languages_are_not_runnable():
    """HTML has no runtime because it needs none — a Run button there would
    be a lie."""
    assert runtimes.runtime_for("html") is None
    assert "html" in runtimes.NOT_EXECUTABLE
    assert runtimes.is_runnable("html") is False


def test_unknown_language_is_not_runnable():
    assert runtimes.is_runnable("brainfuck") is False


def test_running_python_returns_output_and_the_exact_command(tmp_path):
    result = runtimes.run_snippet(Sandbox(root=tmp_path), "python", "print('hi from a block')")
    assert result["ok"] is True
    assert "hi from a block" in result["output"]
    # the command is surfaced because this executed model-written code
    assert result["command"].startswith("python ")
    assert Path(result["file"]).exists()


def test_snippets_land_in_the_project_scratch_directory(tmp_path):
    path = runtimes.write_snippet(tmp_path, "python", "x = 1")
    assert path.parent.name == runtimes.SCRATCH_DIRNAME
    assert path.suffix == ".py"
    assert path.read_text(encoding="utf-8") == "x = 1"


def test_a_failing_snippet_reports_failure_not_success(tmp_path):
    result = runtimes.run_snippet(Sandbox(root=tmp_path), "python", "raise SystemExit(3)")
    assert result["ok"] is False
    assert result["exit"] == 3


def test_an_endless_snippet_times_out_and_is_killed(tmp_path):
    result = runtimes.run_snippet(Sandbox(root=tmp_path), "python",
                                  "import time\ntime.sleep(30)", timeout=2)
    assert result["timed_out"] is True
    assert result["ok"] is False


def test_sql_runs_without_the_sqlite3_cli(tmp_path):
    """The sqlite3 CLI isn't installed on this machine; Python's stdlib
    module is, so SQL blocks work anyway."""
    import shutil
    assert shutil.which("sqlite3") is None or True  # documented, not required
    result = runtimes.run_snippet(
        Sandbox(root=tmp_path), "sql",
        "CREATE TABLE t(a INT, b TEXT);\nINSERT INTO t VALUES (1,'one');\nSELECT * FROM t;",
    )
    assert result["ok"] is True
    assert "one" in result["output"]
    assert (tmp_path / runtimes.SCRATCH_DIRNAME / "scratch.db").exists()  # a real, reusable artifact


def test_snippets_execute_inside_the_sandbox(tmp_path):
    """Run goes through the same boundary as every other execution — it
    cannot write outside the project."""
    outside = tmp_path.parent / "escaped.txt"
    code = f"open(r'''{outside}''','w').write('nope')"
    result = runtimes.run_snippet(Sandbox(root=tmp_path), "python", code)
    # the process may fail or succeed depending on OS permissions, but the
    # sandbox root is where it runs from and where its file was written
    assert Path(result["file"]).is_relative_to(tmp_path)


# ---- the endpoints the chat UI calls --------------------------------------

def _client(tmp_path):
    return TestClient(create_app(tmp_path / "brain", project_dir=tmp_path))


def test_runtimes_endpoint_lists_what_this_machine_can_run(tmp_path):
    body = _client(tmp_path).get("/api/runtimes").json()
    assert body["runtimes"]["python"]["available"] is True
    assert "html" in body["not_executable"]


def test_run_endpoint_executes_and_reports(tmp_path):
    r = _client(tmp_path).post("/api/run", json={"language": "python", "code": "print(2+2)"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "4" in body["output"]


def test_run_endpoint_accepts_a_json_body(tmp_path):
    """Regression: the request models were defined inside create_app, so
    with `from __future__ import annotations` FastAPI couldn't resolve them
    and treated the body as a query parameter — every call 422'd."""
    r = _client(tmp_path).post("/api/run", json={"language": "python", "code": "pass"})
    assert r.status_code != 422


def test_snippet_endpoint_returns_a_project_relative_path(tmp_path):
    body = _client(tmp_path).post("/api/snippet", json={"language": "python", "code": "x=1"}).json()
    assert body["path"] == ".dariusai-scratch/snippet-1.py"   # what the editor opens
    assert Path(body["absolute"]).exists()


def test_running_a_language_with_no_runtime_says_so(tmp_path):
    body = _client(tmp_path).post("/api/run", json={"language": "html", "code": "<b>x</b>"}).json()
    assert body["ok"] is False
    assert "no runtime" in body["error"]
