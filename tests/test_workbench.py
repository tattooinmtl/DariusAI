"""Project creation: naming, typeahead, scaffolding, SQLite, setup steps."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.brain.store import BrainStore
from dariusai.viz.server import create_app
from dariusai.workbench import (
    DEFAULT_WORKBENCH_ROOT,
    InvalidProjectName,
    ProjectExists,
    create_project,
    sanitize_name,
    search,
    workbench_root,
)

PAGE = (Path(__file__).resolve().parents[1] / "src/dariusai/viz/static/index.html").read_text(encoding="utf-8")


# ---- where projects live --------------------------------------------------

def test_workbench_root_defaults_to_the_drive_root_not_inside_the_app():
    """Asked for explicitly: projects must not be tangled up in DariusAI's
    own source tree."""
    assert DEFAULT_WORKBENCH_ROOT == Path("C:/DariusAIWorkbench")
    assert "dariusai-harness" not in str(DEFAULT_WORKBENCH_ROOT).lower()


def test_workbench_root_is_overridable(tmp_path):
    store = BrainStore(tmp_path / "brain")
    store.set_setting("workbench_root", str(tmp_path / "Elsewhere"))
    assert workbench_root(store) == tmp_path / "Elsewhere"


# ---- names ----------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("My First Project", "My-First-Project"),
    ("  spaced  out  ", "spaced-out"),
    ("weird/\\:*?name", "weirdname"),
    ("dots...", "dots"),
])
def test_names_become_usable_folder_names(given, expected):
    assert sanitize_name(given) == expected


@pytest.mark.parametrize("bad", ["", "   ", "///", "..."])
def test_a_name_with_nothing_usable_is_refused(bad):
    with pytest.raises(InvalidProjectName):
        sanitize_name(bad)


# ---- typeahead ------------------------------------------------------------

def test_h_reaches_the_whole_html_family():
    labels = [t.label for t in search("h")]
    assert labels[:3] == ["HTML", "HTML5 (semantic starter)", "HTMX"]


def test_htm_narrows_to_the_html_family():
    ids = {t.id for t in search("htm")}
    assert {"html", "html5", "htmx"} <= ids
    assert "python" not in ids


def test_py_reaches_the_python_family():
    ids = [t.id for t in search("py")]
    assert set(ids) == {"python", "flask", "fastapi"}


def test_prefix_matches_outrank_substring_matches():
    """Typing 'h' must land on HTML, not on something that merely contains
    an h like 'Bash'."""
    assert search("h")[0].id == "html"


def test_empty_query_lists_everything():
    assert len(search("")) == len(search(None)) > 10


# ---- creation -------------------------------------------------------------

def test_creates_files_and_reports_done(tmp_path):
    events = []
    result = create_project("Site One", "html", events.append, root=tmp_path)

    project = tmp_path / "Site-One"
    assert result["ok"] is True
    assert (project / "index.html").is_file()
    assert (project / "styles.css").is_file()
    assert (project / "README.md").is_file()
    assert (project / ".gitignore").is_file()
    assert any(e["type"] == "log" for e in events)


def test_sqlite_projects_get_a_real_initialised_database(tmp_path):
    """sqlite3 comes from Python's standard library, so this works without
    the sqlite3 CLI being installed."""
    import sqlite3
    create_project("DataProj", "sqlite", lambda e: None, root=tmp_path)

    db = tmp_path / "DataProj" / "data" / "app.db"
    assert db.is_file()
    con = sqlite3.connect(db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "items" in tables


def test_refuses_to_overwrite_an_existing_project(tmp_path):
    create_project("Twice", "html", lambda e: None, root=tmp_path)
    with pytest.raises(ProjectExists):
        create_project("Twice", "html", lambda e: None, root=tmp_path)


def test_unknown_type_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        create_project("X", "cobol-on-cogs", lambda e: None, root=tmp_path)


def test_a_missing_runtime_still_scaffolds_but_skips_setup(tmp_path, monkeypatch):
    """Files are useful even when the toolchain isn't there; silently
    'succeeding' on a setup that never ran would not be."""
    import dariusai.workbench.create as wc
    monkeypatch.setattr(wc, "detect", lambda: {"go": {"available": False, "label": "Go"}})

    events = []
    result = create_project("GoProj", "go", events.append, root=tmp_path)

    assert result["ok"] is False
    assert (tmp_path / "GoProj" / "main.go").is_file()
    assert any("not installed" in e.get("line", "") for e in events if e["type"] == "log")


def test_setup_steps_stream_their_output(tmp_path):
    """The console must fill as work happens, not all at the end."""
    events = []
    create_project("StreamProj", "javascript", events.append, root=tmp_path)

    logs = [e for e in events if e["type"] == "log"]
    assert any(e["stream"] == "cmd" and "node --version" in e["line"] for e in logs)
    assert any(e["type"] == "step" for e in events)


# ---- the API --------------------------------------------------------------

def test_workbench_endpoint_lists_types_with_real_availability(tmp_path):
    client = TestClient(create_app(tmp_path / "brain", project_dir=tmp_path))
    body = client.get("/api/workbench").json()

    ids = {t["id"] for t in body["types"]}
    assert {"html", "html5", "htmx", "python", "flask", "sqlite"} <= ids
    html = [t for t in body["types"] if t["id"] == "html"][0]
    assert html["available"] is True          # needs no runtime at all
    python = [t for t in body["types"] if t["id"] == "python"][0]
    assert python["needs_env"] is True and python["sqlite"] is True


# ---- the form -------------------------------------------------------------

def test_the_form_has_the_pieces_that_were_asked_for():
    assert 'data-action="new-project"' in PAGE       # the button
    assert "id='npName'" in PAGE                      # Name your Project
    assert "id='npTypeSearch'" in PAGE                # type picker with typeahead
    assert "id='npConsole'" in PAGE                   # embedded setup console
    assert "/ws/workbench" in PAGE                    # streamed, not batched


# ---- the agent can work the workbench too ---------------------------------

def test_agent_gets_workbench_tools(tmp_path):
    from dariusai.agent.sandbox import Sandbox
    from dariusai.agent.tools import build_tool_registry

    reg = build_tool_registry(BrainStore(tmp_path / "brain"), Sandbox(root=tmp_path))
    assert {"list_projects", "project_types", "create_project"} <= set(reg.specs)


def test_agent_can_list_and_create_projects(tmp_path):
    from dariusai.agent.sandbox import Sandbox
    from dariusai.agent.tools import build_tool_registry

    bench = tmp_path / "DariusAIWorkbench"
    bench.mkdir()
    store = BrainStore(tmp_path / "brain")
    store.set_setting("workbench_root", str(bench))
    reg = build_tool_registry(store, Sandbox(root=bench))

    assert "empty" in reg.call("list_projects", {})

    out = reg.call("create_project", {"name": "Agent Made This", "project_type": "html"})
    assert "created" in out
    assert (bench / "Agent-Made-This" / "index.html").is_file()

    listing = reg.call("list_projects", {})
    assert "Agent-Made-This" in listing


def test_agent_is_told_which_types_are_unavailable(tmp_path):
    from dariusai.agent.sandbox import Sandbox
    from dariusai.agent.tools import build_tool_registry
    reg = build_tool_registry(BrainStore(tmp_path / "brain"), Sandbox(root=tmp_path))
    listing = reg.call("project_types", {})
    assert "html" in listing and "python" in listing


def test_agent_create_project_reports_errors_rather_than_raising(tmp_path):
    from dariusai.agent.sandbox import Sandbox
    from dariusai.agent.tools import build_tool_registry
    store = BrainStore(tmp_path / "brain")
    store.set_setting("workbench_root", str(tmp_path / "bench"))
    reg = build_tool_registry(store, Sandbox(root=tmp_path))
    assert "ERROR" in reg.call("create_project", {"name": "x", "project_type": "nonsense"})


# ---- the boundary the agent works inside ----------------------------------

def test_a_project_inside_the_workbench_makes_the_workbench_the_root(tmp_path):
    """So the agent can see sibling projects and create new ones."""
    from dariusai.agent.sandbox import Sandbox
    bench = tmp_path / "bench"
    (bench / "ProjA").mkdir(parents=True)
    (bench / "ProjB").mkdir()

    sb = Sandbox.for_workspace(bench / "ProjA", bench)
    assert sb.root == bench.resolve()
    assert sb.resolve(str(bench / "ProjB"))          # sibling reachable


def test_a_folder_opened_outside_the_workbench_stays_confined_to_itself(tmp_path):
    """An unrelated directory is not an invitation to roam the disk."""
    from dariusai.agent.sandbox import Sandbox, SandboxViolation
    bench = tmp_path / "bench"
    bench.mkdir()
    other = tmp_path / "SomeOtherRepo"
    other.mkdir()

    sb = Sandbox.for_workspace(other, bench)
    assert sb.root == other.resolve()
    with pytest.raises(SandboxViolation):
        sb.resolve(str(bench))


def test_the_form_can_be_closed():
    """It rendered with no way out: openModal only wires [data-close] on its
    generic branch, and this modal renders itself."""
    assert "close-row" in PAGE.split("Create New Project")[1][:400]
    body = PAGE.split("function renderNewProjectModal")[1][:900]
    assert "[data-close]" in body and "closeModal" in body


def test_the_name_is_cleared_after_a_successful_create():
    """Leaving it filled made the next click a guaranteed 'already exists'."""
    body = PAGE.split("function createProject")[1][:2600]
    assert "nameEl.value = \"\"" in body
