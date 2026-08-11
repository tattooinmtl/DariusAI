"""Activity events must name the nodes they concern.

The bug: every tool call published `route: "brain-coordinator"` and nothing
else, so the viz had no way to know *which* skill was involved. Combined
with a star-shaped graph, "the agent is thinking" lit every edge in the
brain at once — a display that was always on and therefore said nothing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent.sandbox import Sandbox
from dariusai.agent.tools import build_tool_registry
from dariusai.brain.skill import Skill
from dariusai.brain.store import BrainStore
from dariusai.events.bus import bus

PAGE = (Path(__file__).resolve().parents[1] / "src/dariusai/viz/static/index.html").read_text(encoding="utf-8")


def _registry(tmp_path):
    store = BrainStore(tmp_path / "brain")
    store.add_skill(Skill(id="rs", title="Rust Ownership", category="language",
                          tags=["rust"], problem="p", solution="s"))
    store.add_skill(Skill(id="py", title="Python Coding", category="language",
                          tags=["python"], problem="p", solution="s"))
    return store, build_tool_registry(store, Sandbox(root=tmp_path))


def _events(kind):
    return [e for e in bus.recent() if e.get("kind") == kind]


def test_search_names_the_candidate_nodes(tmp_path):
    _, reg = _registry(tmp_path)
    reg.call("search_brain", {"query": "rust"})

    ev = _events("brain_search")[-1]
    assert ev["query"] == "rust"
    assert ev["ids"] == ["rs"]          # only what matched
    assert "py" not in ev["ids"]


def test_loading_a_skill_names_that_one_node(tmp_path):
    _, reg = _registry(tmp_path)
    reg.call("load_skill", {"skill_id": "rs"})

    ev = _events("skill_used")[-1]
    assert ev["id"] == "rs"
    assert ev["label"] == "Rust Ownership"


def test_a_search_that_matches_nothing_still_reports_honestly(tmp_path):
    _, reg = _registry(tmp_path)
    reg.call("search_brain", {"query": "cobol"})
    assert _events("brain_search")[-1]["ids"] == []


def test_tool_calls_route_to_dedicated_tool_node(tmp_path):
    store, reg = _registry(tmp_path)
    reg.call("list_dir", {"path": str(tmp_path)})

    ev = _events("tool_call")[-1]
    assert ev["tool"] == "list_dir"
    assert ev["route"] == "tool-list_dir"

    payload = store.to_graph_payload()
    assert any(n["id"] == "tool-list_dir" and n["category"] == "tool" for n in payload["nodes"])


def test_web_research_tool_node_exists_in_graph(tmp_path):
    store, _ = _registry(tmp_path)
    payload = store.to_graph_payload()
    assert any(n["id"] == "tool-web_research" and n["category"] == "tool" for n in payload["nodes"])


# ---- the viz side of the contract -----------------------------------------

def test_edges_electrify_from_use_not_from_thinking():
    """The regression that mattered: `thinking && touches the coordinator`
    is true for every edge of a star graph."""
    assert "thinking && (a.id === COORDINATOR_ID" not in PAGE
    assert "var electrified = (inUseUntil.get(a.id) > now) || (inUseUntil.get(b.id) > now);" in PAGE


def test_considered_and_used_are_visually_distinct():
    assert 'data.kind === "brain_search"' in PAGE
    assert 'data.kind === "skill_used"' in PAGE
    # candidates blink without earning the sustained in-use glow
    assert 'flashNode(id, "#7c84ab", 420, false)' in PAGE
