"""omni skill import + the operating doctrine that's spliced into every
agent prompt."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent import doctrine
from dariusai.agent.chat import CHAT_SYSTEM
from dariusai.agent.graph import CODER_SYSTEM, PLANNER_SYSTEM
from dariusai.agent.tools import build_tool_registry
from dariusai.brain.omni_import import category_for, import_skills, parse_skill_file
from dariusai.brain.store import BrainStore

SKILL_MD = """---
name: python-coding
command: /python
description: Python environment setup, syntax rules, and project scaffolding.
---

# Python Coding Skill

## Purpose
Guide the user through Python environment detection.
"""


def write_skill(root: Path, name: str, text: str = SKILL_MD) -> Path:
    d = root / name
    d.mkdir(parents=True)
    path = d / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_frontmatter_and_body_map_onto_the_skill_node(tmp_path):
    skill = parse_skill_file(write_skill(tmp_path, "python-coding"))
    assert skill.id == "omni-python-coding"
    assert skill.title == "Python Coding Skill"          # taken from the H1
    assert skill.category == "language"
    assert "Python environment setup" in skill.problem   # description -> problem
    assert "Guide the user through" in skill.solution    # body -> solution
    assert "/python" in skill.tags                       # command is searchable
    assert "omni" in skill.tags
    assert skill.tool_generated == "omni-import"


def test_body_is_kept_whole_not_summarised(tmp_path):
    """A skill's value is its specifics; a summary drops exactly those."""
    body = "\n".join(f"step {i}: run the exact command foo --flag={i}" for i in range(40))
    text = f"---\nname: big\ndescription: d\n---\n\n# Big\n\n{body}\n"
    skill = parse_skill_file(write_skill(tmp_path, "big", text))
    for i in range(40):
        assert f"foo --flag={i}" in skill.solution


def test_category_rules():
    assert category_for("rust-coding") == "language"
    assert category_for("code-review") == "tool"
    assert category_for("high-end-visual-design") == "pattern"
    assert category_for("okf-knowledge") == "skill"


def test_import_is_idempotent(tmp_path):
    src = tmp_path / "skills"
    write_skill(src, "python-coding")
    write_skill(src, "rust-coding")
    store = BrainStore(tmp_path / "brain")

    first = import_skills(store, src)
    second = import_skills(store, src)

    assert first["imported"] == second["imported"] == 2
    ids = [n["id"] for n in store.to_graph_payload()["nodes"]]
    assert ids.count("omni-python-coding") == 1  # re-import overwrites, never duplicates


def test_imported_skills_are_searchable_then_loadable(tmp_path):
    """The deferred-loading pattern: search returns labels, load pulls the
    body — so the brain can grow without growing the prompt."""
    src = tmp_path / "skills"
    write_skill(src, "python-coding")
    store = BrainStore(tmp_path / "brain")
    import_skills(store, src)
    reg = build_tool_registry(store)

    found = reg.call("search_brain", {"query": "python"})
    assert "omni-python-coding" in found
    assert "Guide the user through" not in found  # body stays out of the search result

    loaded = reg.call("load_skill", {"skill_id": "omni-python-coding"})
    assert "Guide the user through" in loaded     # ...until it's asked for


def test_load_skill_on_a_bad_id_explains_itself(tmp_path):
    reg = build_tool_registry(BrainStore(tmp_path / "brain"))
    assert "search_brain" in reg.call("load_skill", {"skill_id": "nope"})


def test_missing_source_directory_is_a_clear_error(tmp_path):
    store = BrainStore(tmp_path / "brain")
    try:
        import_skills(store, tmp_path / "nothing-here")
    except FileNotFoundError as exc:
        assert "no skills directory" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


# ---- doctrine is baked into every entry point, not just one ---------------

def test_every_agent_prompt_carries_the_doctrine():
    for prompt in (PLANNER_SYSTEM, CODER_SYSTEM, CHAT_SYSTEM):
        assert doctrine.DOCTRINE in prompt


def test_doctrine_states_the_four_parts_and_both_rules():
    text = doctrine.DOCTRINE
    for part in ("harness:", "sandbox:", "model:", "loop:"):
        assert part in text
    assert "isn't broken, don't touch it" in text
    assert "isn't needed, don't do it" in text
    assert "search_brain first" in text and "load_skill" in text
    assert "index a conversation node" in text
    assert "branch from OKF" in text


def test_a_skill_body_containing_markdown_headings_round_trips(tmp_path):
    """Regression: section parsing used to stop at any `##`, so a skill whose
    content had its own headings came back truncated — the file on disk was
    complete, reading it was lossy."""
    from dariusai.brain.skill import Skill

    body = "intro\n\n## Phase 1\nfirst\n\n## Phase 2\nsecond\n\n### Deep\nthird"
    original = Skill(id="x-1", title="X", solution=body, problem="p", best_practices="bp")
    restored = Skill.from_markdown(original.to_markdown())

    assert restored.solution == body
    assert restored.best_practices == "bp"  # the real section after it still parses


# ---- the addon library ----------------------------------------------------

def test_addon_import_builds_a_branching_library(tmp_path):
    """48 skills across 5 groups must become a tree, not 48 more spokes."""
    from dariusai.brain.omni_import import import_addon
    from dariusai.brain.store import COORDINATOR_ID, BrainStore

    root = Path(__file__).resolve().parents[1] / "addon"
    if not root.is_dir():
        import pytest as _pytest
        _pytest.skip("no addon directory in this checkout")

    store = BrainStore(tmp_path / "brain")
    result = import_addon(store, root)

    assert result["imported"] >= 40
    assert result["hooks"] >= 1
    assert len(result["groups"]) >= 3

    payload = store.to_graph_payload()
    edges = {(e["source"], e["target"]) for e in payload["edges"]}
    spokes = [n["id"] for n in payload["nodes"] if (COORDINATOR_ID, n["id"]) in edges]
    # Only group branches and the named trunks touch the centre. The trunks are
    # `hooks` (guards, not knowledge) and the OKF anchor (conversations branch
    # from it), which is why they are exempt rather than filed under a group.
    from dariusai.brain.omni_import import TRUNK_IDS

    trunks = {s for s in spokes if s in TRUNK_IDS}
    assert all(
        s.startswith("addon-group-") or s in TRUNK_IDS for s in spokes
    ), f"skills reaching the centre directly: {sorted(s for s in spokes if not s.startswith('addon-group-') and s not in TRUNK_IDS)}"
    assert len(spokes) == len(result["groups"]) + len(trunks), f"too many spokes: {sorted(spokes)}"


def test_addon_skills_branch_from_their_group(tmp_path):
    from dariusai.brain.omni_import import import_addon
    from dariusai.brain.store import BrainStore

    root = Path(__file__).resolve().parents[1] / "addon"
    if not root.is_dir():
        import pytest as _pytest
        _pytest.skip("no addon directory in this checkout")

    store = BrainStore(tmp_path / "brain")
    import_addon(store, root)
    edges = {(e["source"], e["target"]) for e in store.to_graph_payload()["edges"]}
    parented = [s for (s, t) in edges if s.startswith("addon-") and t.startswith("addon-group-")]
    assert len(parented) >= 40


def test_hooks_are_their_own_category(tmp_path):
    """Hooks are guards that run before a command, not knowledge — filing
    them as skills would make the graph misdescribe what it holds."""
    from dariusai.brain.omni_import import import_addon
    from dariusai.brain.store import BrainStore

    root = Path(__file__).resolve().parents[1] / "addon"
    if not root.is_dir():
        import pytest as _pytest
        _pytest.skip("no addon directory in this checkout")

    store = BrainStore(tmp_path / "brain")
    import_addon(store, root)
    nodes = [n for n in store.to_graph_payload()["nodes"] if n["category"] == "hook"]
    # The trunk shares the category on purpose, so toggling "hook" in the
    # legend hides the branch along with everything hanging off it.
    trunk = [n for n in nodes if n["id"] == "hooks"]
    leaves = [n for n in nodes if n["id"] != "hooks"]
    assert trunk, "the hooks trunk should be filterable with its hooks"
    assert leaves, "no hook nodes"
    assert all(h["id"].startswith("hook-") for h in leaves)


def test_nested_and_flat_skill_trees_are_both_walked(tmp_path):
    from dariusai.brain.omni_import import group_of, iter_skill_files

    flat = tmp_path / "flat" / "alpha"
    flat.mkdir(parents=True)
    (flat / "SKILL.md").write_text("---\nname: alpha\n---\n# Alpha\n", encoding="utf-8")
    nested = tmp_path / "nested" / "group" / "beta"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("---\nname: beta\n---\n# Beta\n", encoding="utf-8")

    assert len(list(iter_skill_files(tmp_path / "flat"))) == 1
    assert group_of(flat / "SKILL.md", tmp_path / "flat") is None
    assert group_of(nested / "SKILL.md", tmp_path / "nested") == "group"


# ---- brain coherence guards (see BrainFIX.md) ------------------------------

def _addon_root():
    root = Path(__file__).resolve().parents[1] / "addon"
    if not root.is_dir():
        import pytest as _pytest
        _pytest.skip("no addon directory in this checkout")
    return root


def test_the_whole_library_is_shipped_and_imports():
    """The 08-09 near-miss was a .gitignore rule that silently staged 0 of 65
    SKILL.md, which would have shipped a harness with an empty brain. Count the
    files on disk and the nodes they become, so that fails loudly here instead
    of on a user's fresh install."""
    from dariusai.brain.omni_import import import_addon, iter_skill_files
    from dariusai.brain.store import BrainStore
    import tempfile

    root = _addon_root()
    on_disk = len(list(iter_skill_files(root / "skills")))
    assert on_disk >= 99, f"skill library shrank: {on_disk} SKILL.md found"

    store = BrainStore(Path(tempfile.mkdtemp()) / "brain")
    result = import_addon(store, root)
    assert result["imported"] == on_disk, "every SKILL.md must become a node"
    assert result["hooks"] >= 1


def test_no_language_guide_is_duplicated(tmp_path):
    """Two guides for one language give the agent two different answers to the
    same question and no basis to choose. Language references live in
    `languages/`; the codebase starters are a different genre under their own
    group, so the pair below must never collide again."""
    root = _addon_root()
    languages = {p.parent.name for p in (root / "skills" / "languages").rglob("SKILL.md")}
    starters = {p.parent.name for p in (root / "skills" / "codebase-starters").rglob("SKILL.md")}
    assert languages, "the languages group is missing"
    assert not (languages & starters), f"same skill folder in two groups: {sorted(languages & starters)}"


def test_superseded_skills_are_archived_and_linked(tmp_path):
    """A superseded skill must stay loadable but stop competing: its
    replacement is recorded as an edge, not merely as prose in its body."""
    from dariusai.brain.omni_import import import_addon
    from dariusai.brain.store import BrainStore

    store = BrainStore(tmp_path / "brain")
    result = import_addon(store, _addon_root())

    assert result["superseded"], "expected at least one superseded_by relation"
    for row in result["superseded"]:
        # the edge exists in the graph
        assert store.graph.superseded_by(row["id"]) == row["superseded_by"]
        # and both ends are real, loadable nodes
        assert store.get_skill(row["id"]).solution
        assert store.get_skill(row["superseded_by"]).solution


def test_addon_skills_are_not_tagged_omni(tmp_path):
    """`omni` was applied to all 99 addon skills: factually wrong, and a tag
    every node carries discriminates nothing."""
    from dariusai.brain.omni_import import import_addon
    from dariusai.brain.store import BrainStore

    store = BrainStore(tmp_path / "brain")
    import_addon(store, _addon_root())
    tagged = [
        n["id"] for n in store.to_graph_payload()["nodes"]
        if n["id"].startswith("addon-") and "omni" in (n.get("tags") or [])
    ]
    assert not tagged, f"addon skills still tagged omni: {tagged[:5]}"


def test_browse_reaches_every_group_and_its_skills(tmp_path):
    """Navigation is the other half of retrieval: the group branches are
    useless to the agent if nothing can descend into them."""
    from dariusai.brain.omni_import import import_addon
    from dariusai.brain.store import COORDINATOR_ID, BrainStore

    store = BrainStore(tmp_path / "brain")
    result = import_addon(store, _addon_root())

    top = store.graph.children_of(COORDINATOR_ID)
    for group_id in result["groups"]:
        assert group_id in top, f"{group_id} is not reachable from the centre"
        assert store.graph.children_of(group_id), f"{group_id} lists no skills"
