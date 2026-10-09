"""The neural view must show what the agent is really doing, as it does it.

Regressions from the 2026-10-09 audit:
- the skill library was looked up under the open project, so with any other
  project open skill_lookup/invoke_skill found nothing at all;
- skill reads were matched to nodes by title, so 5 of 181 skills ever lit;
- tool calls were announced only after they finished;
- every usage bump reloaded the whole graph from SQLite.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent import tools as T
from dariusai.agent.sandbox import Sandbox
from dariusai.brain.omni_import import import_addon
from dariusai.brain.store import COORDINATOR_ID, BrainStore
from dariusai.events.bus import bus

SKILL_MD = """---
name: {name}
description: {desc}
---

# {title}

## When to use

{body}
"""


def _addon(root: Path) -> Path:
    """A tiny addon tree: one group, two skills whose titles differ from
    their folder names — the case the title match got wrong."""
    for name, title, body in (
        ("tdd-loop", "Test-Driven Development Loop", "Write the failing test first, then make it pass."),
        ("ownership", "Rust Ownership Model", "Borrow checker rules for moving and borrowing values."),
    ):
        d = root / "addon" / "skills" / "superpowers" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            SKILL_MD.format(name=name, desc=title, title=title, body=body), encoding="utf-8")
    return root


def _store(tmp_path, monkeypatch, project_dir: Path):
    install = _addon(tmp_path / "install")
    monkeypatch.setattr(T, "INSTALL_ROOT", install)
    store = BrainStore(tmp_path / "brain")
    import_addon(store, install / "addon")
    store.set_setting("project_dir", str(project_dir))
    return store


def _events(kind):
    return [e for e in bus.recent() if e.get("kind") == kind]


def test_skills_are_found_with_an_unrelated_project_open(tmp_path, monkeypatch):
    elsewhere = tmp_path / "MyGame"
    elsewhere.mkdir()
    store = _store(tmp_path, monkeypatch, elsewhere)

    names = sorted(p.parent.name for p in T._skill_files(store))
    assert names == ["ownership", "tdd-loop"]
    assert not str(T._resolve_skill_path(store, "tdd-loop")).startswith("no skill")


def test_project_skill_shadows_the_installed_one(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    own = project / "addon" / "skills" / "local" / "tdd-loop"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("---\nname: tdd-loop\n---\n# mine\n", encoding="utf-8")
    store = _store(tmp_path, monkeypatch, project)

    assert T._resolve_skill_path(store, "tdd-loop") == own / "SKILL.md"
    assert sum(p.parent.name == "tdd-loop" for p in T._skill_files(store)) == 1


def test_lookup_lights_the_skill_and_walks_its_branch(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, tmp_path)
    bus.clear()

    out = T._skill_lookup(store, "failing test first")
    assert "passage" in out

    used = _events("skill_used")
    assert [e["id"] for e in used] == ["addon-tdd-loop"]
    assert used[0]["label"] == "Test-Driven Development Loop"
    assert used[0]["path"] == [COORDINATOR_ID, "addon-group-superpowers", "addon-tdd-loop"]


def test_tool_calls_announce_start_and_end(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, tmp_path)
    reg = T.build_tool_registry(store, Sandbox(root=tmp_path))
    bus.clear()

    reg.call("list_dir", {"path": "."})
    kinds = [e["kind"] for e in bus.recent() if e.get("tool") == "list_dir"]
    assert kinds == ["tool_start", "tool_call"]
    end = _events("tool_call")[-1]
    assert end["ok"] is True and end["id"] == "tool-list_dir"
    assert isinstance(end["ms"], int)


def test_a_failing_tool_still_ends(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, tmp_path)
    reg = T.build_tool_registry(store, Sandbox(root=tmp_path))
    bus.clear()

    result = reg.call("read_file", {"nonsense_arg": 1})
    assert result.startswith("ERROR")
    assert _events("tool_start")[-1]["tool"] == "read_file"
    assert _events("tool_call")[-1]["ok"] is False


def test_usage_bump_updates_the_graph_without_a_reload(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, tmp_path)
    reloads = []
    original = store._load_graph
    monkeypatch.setattr(store, "_load_graph", lambda: (reloads.append(1), original())[1])

    before = store.graph.graph.nodes["addon-ownership"]["usage_count"]
    store.touch_usage("addon-ownership")
    assert store.graph.graph.nodes["addon-ownership"]["usage_count"] == before + 1
    assert reloads == []
    # and the database agrees, so the next full load sees the same number
    row = store.conn.execute("SELECT usage_count FROM nodes WHERE id='addon-ownership'").fetchone()
    assert row[0] == before + 1


def test_lineage_of_an_orphan_is_one_hop(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch, tmp_path)
    assert store.graph.lineage("addon-group-superpowers") == [COORDINATOR_ID, "addon-group-superpowers"]
    assert store.graph.lineage("no-such-node") == []
