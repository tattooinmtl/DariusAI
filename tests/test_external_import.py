"""Tests for the external skills import.

The user's external_skills/ folder is the drop-in target for skills from
other agents. The import must:
- Find flat and grouped SKILL.md files at any depth.
- Build branch nodes for each agent folder so the brain doesn't get
  another 50 spokes around the centre.
- Derive ids from `<agent>-<skill>` so re-imports overwrite cleanly.
- Carry the agent tag and a small frontmatter-derived tag set, not the
  'omni' tag from the addon path (it's simply wrong here).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.brain.omni_import import EXTERNAL_ID_PREFIX, import_external
from dariusai.brain.store import BrainStore


def _write_skill(root: Path, agent_or_name: str, skill_name: str, *, body: str = "do the thing", flat: bool = False) -> None:
    if "/" in agent_or_name:
        agent, name = agent_or_name.split("/", 1)
        d = root / agent / name
    elif flat:
        d = root / agent_or_name
    else:
        # Grouped layout: <root>/<agent>/<skill_name>/SKILL.md
        d = root / agent_or_name / skill_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: external skill {skill_name}\n---\n\n# {skill_name}\n\n{body}\n",
        encoding="utf-8",
    )


def test_external_import_grouped_creates_one_branch_per_agent(tmp_path):
    root = tmp_path / "external_skills"
    _write_skill(root, "claude-code", "react-best-practices")
    _write_skill(root, "claude-code", "hooks-101")
    _write_skill(root, "cursor", "agent-mode")

    store = BrainStore(tmp_path / "brain")
    result = import_external(store, root)

    assert result["imported"] == 3
    assert set(result["agents"]) == {"claude-code", "cursor"}
    assert set(result["branches"]) == {"extsk-group-claude-code", "extsk-group-cursor"}

    # Branch nodes exist and the skills hang off them.
    for branch_id in result["branches"]:
        assert store.get_skill(branch_id), f"missing branch {branch_id}"

    # Id pattern: extsk-<agent>-<skill>.
    rows = store.search("external", limit=20)
    ids = [r["id"] for r in rows]
    assert "extsk-claude-code-react-best-practices" in ids
    assert "extsk-claude-code-hooks-101" in ids
    assert "extsk-cursor-agent-mode" in ids

    # The 'omni' tag from parse_skill_file is wrong here — these are not
    # omni skills. The 'external' tag is added by the import.
    claude_row = next(r for r in rows if r["id"] == "extsk-claude-code-react-best-practices")
    assert "omni" not in claude_row["tags"]
    assert "external" in claude_row["tags"]
    assert "claude-code" in claude_row["tags"]


def test_external_import_flat_skills_land_under_external_category(tmp_path):
    root = tmp_path / "external_skills"
    _write_skill(root, "lone-skill", "lone-skill", flat=True)

    store = BrainStore(tmp_path / "brain")
    result = import_external(store, root)

    assert result["imported"] == 1
    assert result["agents"] == []
    assert result["branches"] == []
    lone = store.get_skill(EXTERNAL_ID_PREFIX + "lone-skill")
    assert lone.category == "external"


def test_external_import_is_idempotent(tmp_path):
    """Re-running must overwrite its own rows, not add duplicates."""
    root = tmp_path / "external_skills"
    _write_skill(root, "claude-code", "react-best-practices", body="v1")
    store = BrainStore(tmp_path / "brain")

    import_external(store, root)
    import_external(store, root)
    import_external(store, root)

    # Single row per (agent, skill), not three.
    rows = store.search("react-best-practices", limit=5)
    matching = [r for r in rows if r["id"] == "extsk-claude-code-react-best-practices"]
    assert len(matching) == 1


def test_external_import_missing_dir_raises(tmp_path):
    store = BrainStore(tmp_path / "brain")
    try:
        import_external(store, tmp_path / "does-not-exist")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError when the folder is absent")
