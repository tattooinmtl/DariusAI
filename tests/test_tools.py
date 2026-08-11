import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent.tools import build_tool_registry
from dariusai.brain.store import BrainStore


def test_write_then_read_file(tmp_path):
    store = BrainStore(tmp_path / "brain")
    reg = build_tool_registry(store)
    target = tmp_path / "out" / "hello.txt"
    result = reg.call("write_file", {"path": str(target), "content": "hi there"})
    assert "wrote" in result
    assert target.read_text() == "hi there"

    read_back = reg.call("read_file", {"path": str(target)})
    assert read_back == "hi there"


def test_list_dir(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    store = BrainStore(tmp_path / "brain")
    reg = build_tool_registry(store)
    listing = reg.call("list_dir", {"path": str(tmp_path)})
    assert "a.txt" in listing
    assert "sub" in listing


def test_run_shell_real_subprocess(tmp_path):
    store = BrainStore(tmp_path / "brain")
    reg = build_tool_registry(store)
    result = reg.call("run_shell", {"command": "python -c \"print(2 + 2)\""})
    assert "exit=0" in result
    assert "4" in result


def test_run_shell_nonzero_exit_reported_not_raised(tmp_path):
    store = BrainStore(tmp_path / "brain")
    reg = build_tool_registry(store)
    result = reg.call("run_shell", {"command": "python -c \"import sys; sys.exit(3)\""})
    assert "exit=3" in result


def test_unknown_tool_returns_error_not_crash(tmp_path):
    store = BrainStore(tmp_path / "brain")
    reg = build_tool_registry(store)
    result = reg.call("does_not_exist", {})
    assert result.startswith("ERROR")


def test_search_brain_tool_finds_seeded_skill(tmp_path):
    from dariusai.brain.skill import Skill
    store = BrainStore(tmp_path / "brain")
    store.add_skill(Skill(id="skill-abc", title="Rust borrow checker gotcha", category="skill", tags=["rust"]))
    reg = build_tool_registry(store)
    result = reg.call("search_brain", {"query": "rust"})
    assert "skill-abc" in result


def test_learn_skill_tool_enforces_min_sources(tmp_path):
    store = BrainStore(tmp_path / "brain")
    reg = build_tool_registry(store)
    result = reg.call("learn_skill", {
        "title": "x", "problem": "p", "solution": "s", "code_examples": "c",
        "best_practices": "b", "edge_cases": "e",
        "sources": [{"url": "https://a.com", "quote": "q"}],
    })
    assert result.startswith("ERROR")


def test_learn_skill_tool_saves_with_enough_sources(tmp_path):
    store = BrainStore(tmp_path / "brain")
    reg = build_tool_registry(store)
    result = reg.call("learn_skill", {
        "title": "Real learned thing", "problem": "p", "solution": "s", "code_examples": "c",
        "best_practices": "b", "edge_cases": "e",
        "sources": [
            {"url": "https://a.com/1", "quote": "q1"}, {"url": "https://b.com/1", "quote": "q2"},
            {"url": "https://c.com/1", "quote": "q3"}, {"url": "https://d.com/1", "quote": "q4"},
            {"url": "https://e.com/1", "quote": "q5"},
        ],
    })
    assert "saved skill" in result
    assert store.search("Real learned thing")
