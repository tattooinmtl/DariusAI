"""Conversations branch off a trunk instead of adding more spokes.

Why this needed fixing: `_load_graph` wired *every* node to the coordinator
with an `index` edge. A `related` edge to a parent was therefore always in
addition to a star edge, and the force layout pulled the node back to the
middle — so a conversation attached to a conversations branch still rendered
as one more spoke. Structure has to exist in the graph before it can show up
in the layout.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.brain.skill import Skill
from dariusai.brain.store import CONVERSATIONS_ROOT, COORDINATOR_ID, BrainStore
from dariusai.events.bus import bus
from dariusai.viz.server import _log_conversation_turn


def _edges(store):
    return {(e["source"], e["target"]) for e in store.to_graph_payload()["edges"]}


def test_a_node_with_a_parent_is_not_also_wired_to_the_centre(tmp_path):
    store = BrainStore(tmp_path / "brain")
    store.add_skill(Skill(id="parent", title="Parent", category="skill"))
    store.add_skill(Skill(id="child", title="Child", category="skill", related=["parent"]))

    edges = _edges(store)
    assert ("child", "parent") in edges
    assert (COORDINATOR_ID, "child") not in edges      # the fix
    assert (COORDINATOR_ID, "parent") in edges         # the trunk still hangs off the centre


def test_an_orphan_still_attaches_to_the_centre(tmp_path):
    """Nothing may float free — a node with no parent belongs to the brain."""
    store = BrainStore(tmp_path / "brain")
    store.add_skill(Skill(id="loner", title="Loner", category="skill"))
    assert (COORDINATOR_ID, "loner") in _edges(store)


def test_a_related_target_that_does_not_exist_does_not_orphan_the_node(tmp_path):
    store = BrainStore(tmp_path / "brain")
    store.add_skill(Skill(id="hopeful", title="Hopeful", category="skill", related=["nowhere"]))
    assert (COORDINATOR_ID, "hopeful") in _edges(store)


def test_ensure_branch_is_idempotent(tmp_path):
    store = BrainStore(tmp_path / "brain")
    first = store.ensure_branch(CONVERSATIONS_ROOT, "Conversations", "conversation")
    second = store.ensure_branch(CONVERSATIONS_ROOT, "Conversations", "conversation")
    assert first == second == CONVERSATIONS_ROOT
    ids = [n["id"] for n in store.to_graph_payload()["nodes"]]
    assert ids.count(CONVERSATIONS_ROOT) == 1


def test_a_recorded_conversation_grows_from_the_branch(tmp_path):
    store = BrainStore(tmp_path / "brain")
    _log_conversation_turn(store, {"user_text": "how do job objects work",
                                   "assistant_text": "They kill a process tree.",
                                   "tool_results": []})

    payload = store.to_graph_payload()
    atoms = [n["id"] for n in payload["nodes"]
             if n["category"] == "conversation" and n["id"] != CONVERSATIONS_ROOT]
    assert len(atoms) == 1
    atom = atoms[0]

    edges = _edges(store)
    assert (atom, CONVERSATIONS_ROOT) in edges                 # branched
    assert (COORDINATOR_ID, atom) not in edges                 # not a spoke
    assert (COORDINATOR_ID, CONVERSATIONS_ROOT) in edges       # trunk on the centre


def test_recording_publishes_the_path_the_charge_travels(tmp_path):
    store = BrainStore(tmp_path / "brain")
    _log_conversation_turn(store, {"user_text": "what is a sandbox",
                                   "assistant_text": "A boundary.", "tool_results": []})

    ev = [e for e in bus.recent() if e.get("kind") == "conversation_logged"][-1]
    assert ev["path"][0] == COORDINATOR_ID
    assert ev["path"][1] == CONVERSATIONS_ROOT
    assert ev["path"][2] == ev["id"]      # brain -> branch -> the new atom
    assert ev["branch"] == CONVERSATIONS_ROOT


def test_many_conversations_share_one_trunk(tmp_path):
    store = BrainStore(tmp_path / "brain")
    for i in range(3):
        _log_conversation_turn(store, {"user_text": f"question {i}",
                                       "assistant_text": f"answer {i}", "tool_results": []})
    nodes = store.to_graph_payload()["nodes"]
    trunks = [n for n in nodes if n["id"] == CONVERSATIONS_ROOT]
    atoms = [n for n in nodes if n["category"] == "conversation" and n["id"] != CONVERSATIONS_ROOT]
    assert len(trunks) == 1
    assert len(atoms) == 3
    edges = _edges(store)
    for atom in atoms:
        assert (atom["id"], CONVERSATIONS_ROOT) in edges


def test_empty_turns_are_not_recorded(tmp_path):
    store = BrainStore(tmp_path / "brain")
    _log_conversation_turn(store, {"user_text": "", "assistant_text": "", "tool_results": []})
    assert [n for n in store.to_graph_payload()["nodes"] if n["category"] == "conversation"] == []


# ---- the viz animates the hops in order, not all at once ------------------

PAGE = (Path(__file__).resolve().parents[1] / "src/dariusai/viz/static/index.html").read_text(encoding="utf-8")


def test_the_charge_walks_the_path_hop_by_hop():
    assert 'data.kind === "conversation_logged"' in PAGE
    assert "hop * 380" in PAGE          # staggered, so it travels outward
    assert PAGE.count('data.kind === "conversation_logged"') == 1  # no duplicate handler
