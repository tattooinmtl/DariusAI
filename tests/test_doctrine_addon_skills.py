"""Tests for the addon library — the domain layer that pairs with
superpowers' process layer.

What's pinned here:

1. The library has been cleaned up: 33 flat-duplicate skills and 21
   external-tool skills (cmux, herdr, fable, codex, corral, etc.) are
   gone. The remaining 92 skills are all reachable via invoke_skill
   and have valid frontmatter.
2. The `using-addon-skills` skill exists, has valid frontmatter, and
   is reachable via invoke_skill.
3. The doctrine's Platform Adaptation block references the new skill
   and gives the correct counts (14 superpowers + 78 domain = 92).
4. Every skill in the library has both a `name:` and `description:`
   field, and the name matches the folder name.
5. Every skill is reachable via invoke_skill() — no dead code.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PROJECT_ROOT / "addon" / "skills"


# ---------------------------------------------------------------------------
# 1. The cleanup produced the right final state
# ---------------------------------------------------------------------------


def test_no_flat_duplicate_skills_remain():
    """33 flat skills were deleted because they were byte-identical
    duplicates of grouped ones, and the invoke_skill glob only finds
    the grouped layout. A regression that re-adds them is caught here."""
    flat = list(SKILLS_ROOT.glob("*/SKILL.md"))
    # Filter out addon/skills/<group>/SKILL.md (top-level within a group)
    # — only catch the truly flat ones at addon/skills/<name>/SKILL.md.
    flat = [p for p in flat if p.parent.parent.parent == SKILLS_ROOT]
    assert flat == [], f"flat skills must not exist: {[p.name for p in flat]}"


def test_rejected_external_tool_skills_do_not_exist():
    """21 skills that referenced external tools unavailable in Dariu
    (cmux, herdr, fable, codex, corral, gpt-review, run-deep-swe,
    pi-web-search, pi-custom-model, macbook-metrics-setup, nuke-cursor-app,
    anti-sleep, etc.) were pruned. A regression that re-adds them is
    caught here."""
    rejected = [
        "agent-orchestration/cmux",
        "agent-orchestration/herdr",
        "agent-orchestration/fable-review",
        "agent-orchestration/fable-safe-prompt",
        "agent-orchestration/gpt-review",
        "agent-orchestration/codex-subagent",
        "agent-orchestration/corral-launch-agents",
        "agent-orchestration/run-deep-swe",
        "ops-and-setup/anti-sleep",
        "ops-and-setup/macbook-metrics-setup",
        "ops-and-setup/nuke-cursor-app",
        "ops-and-setup/pi-custom-model",
        "ops-and-setup/google-safe-browsing",
        "research-and-web/deepapi",
        "research-and-web/deep-research",
        "research-and-web/online-shopping",
        "research-and-web/fireflies-transcript",
        "research-and-web/pi-web-search",
        "research-and-web/youtube-transcript",
        "skill-authoring/distribute-skill-to-all-agents",
        "skill-authoring/folder-specific-claude-and-agents-md",
    ]
    for r in rejected:
        path = SKILLS_ROOT / r
        assert not path.exists(), f"rejected skill back on disk: {r}"


def test_skill_count_is_93():
    """The cleanup landed 92 skills — 14 superpowers + 78 addon. The
    93rd is `gamedev/3dgame`, added with the Blender MCP bridge.
    A regression that adds or removes skills silently is caught here."""
    skills = list(SKILLS_ROOT.glob("*/*/SKILL.md"))
    assert len(skills) == 93, f"expected 93 skills, got {len(skills)}"


def test_skill_count_breakdown_by_group():
    """Pin the per-group counts so an accidental mass delete is caught."""
    expected = {
        "superpowers": 14,
        "agent-orchestration": 5,
        "codebase-starters": 10,
        "design": 4,
        "gamedev": 7,   # +1: 3dgame, the Blender asset kit
        "languages": 21,
        "ops-and-setup": 6,
        "research-and-web": 2,
        "skill-authoring": 4,
        "thinking-and-docs": 12,
        "tooling": 7,
        "archive": 1,
    }
    actual = {}
    for group_dir in sorted(SKILLS_ROOT.iterdir()):
        if not group_dir.is_dir():
            continue
        count = len(list(group_dir.glob("*/SKILL.md")))
        actual[group_dir.name] = count
    assert actual == expected, f"group counts drifted: {actual}"


# ---------------------------------------------------------------------------
# 2. The new using-addon-skills skill
# ---------------------------------------------------------------------------


def test_using_addon_skills_exists_and_has_frontmatter():
    """The bootstrap for the domain layer was added in this session."""
    path = SKILLS_ROOT / "skill-authoring" / "using-addon-skills" / "SKILL.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---"), "SKILL.md must start with frontmatter"
    m = re.search(r"^name:\s*(\S+)", text, re.M)
    assert m and m.group(1) == "using-addon-skills"
    m = re.search(r"^description:\s*(.+?)(?=^[a-z\-]+:|^---)", text, re.M | re.S)
    assert m and m.group(1).strip(), "description is required"


def test_using_addon_skills_documents_the_pruned_skills():
    """The skill lists the deleted skills so the user can see what was
    removed and why."""
    path = SKILLS_ROOT / "skill-authoring" / "using-addon-skills" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    # The "What was pruned" section mentions each rejected category.
    assert "What was pruned" in text
    for keyword in ("cmux", "herdr", "fable", "codex", "corral", "gpt-review",
                    "run-deep-swe", "macOS", "DeepAPI"):
        assert keyword in text, f"prune list missing {keyword!r}"


# ---------------------------------------------------------------------------
# 3. The doctrine references the new skill and has the right counts
# ---------------------------------------------------------------------------


def test_doctrine_references_using_addon_skills():
    """The Platform Adaptation block in the doctrine must point at
    the new bootstrap so the model knows to invoke it."""
    from dariusai.agent import doctrine
    assert "using-addon-skills" in doctrine.SUPERPOWERS_BOOTSTRAP
    assert "invoke_skill" in doctrine.SUPERPOWERS_BOOTSTRAP


def test_doctrine_does_not_hardcode_a_skill_count():
    """The doctrine used to carry a library census ("14 superpowers + 78
    addon = 92"). It was removed on 2026-08-19: the numbers drifted the
    moment a skill was added — the library holds 185 today — and a count
    is not something the model can act on. The doctrine points at the
    live tree instead, which is always right and costs no prompt bytes.
    """
    from dariusai.agent import doctrine
    bootstrap = doctrine.SUPERPOWERS_BOOTSTRAP
    for stale in ("14 skills", "78 more", "92 skills"):
        assert stale not in bootstrap, f"doctrine re-hardcoded a skill count: {stale!r}"
    assert "browse_brain" in bootstrap, "doctrine must point at the live tree instead of a count"


# ---------------------------------------------------------------------------
# 4. Every skill has valid frontmatter
# ---------------------------------------------------------------------------


def _all_skills() -> list[Path]:
    return sorted(SKILLS_ROOT.glob("*/*/SKILL.md"))


def test_every_skill_has_frontmatter():
    """No SKILL.md without a `---` frontmatter block — the brain
    needs the name and description to index and route the skill."""
    for path in _all_skills():
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---"), f"{path} missing frontmatter"


def test_every_skill_has_name_field():
    """Every SKILL.md must declare a `name:` field. The brain reads
    this to label the node; without it the skill is undisplayable."""
    for path in _all_skills():
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^name:\s*(\S+)", text, re.M)
        assert m, f"{path} missing name: field"


def test_every_skill_name_matches_folder():
    """The skill's `name:` field must match its folder name. A drift
    here means the brain's index and the on-disk path disagree —
    invoke_skill finds the path but the brain label is wrong."""
    for path in _all_skills():
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^name:\s*(\S+)", text, re.M)
        assert m.group(1) == path.parent.name, (
            f"{path}: name={m.group(1)!r} but folder={path.parent.name!r}"
        )


def test_every_skill_has_description():
    """Every SKILL.md must have a `description:` field. The
    auto-trigger rules in `using-addon-skills` key off these
    descriptions; without one the skill is unrouted."""
    for path in _all_skills():
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^description:\s*(.+?)(?=^[a-z\-]+:|^---)", text, re.M | re.S)
        assert m and m.group(1).strip(), f"{path} missing description"


# ---------------------------------------------------------------------------
# 5. Every skill is reachable via invoke_skill
# ---------------------------------------------------------------------------


@pytest.fixture
def brain_store(tmp_path):
    from dariusai.brain.store import BrainStore
    home = tmp_path / "brain"
    home.mkdir()
    store = BrainStore(home)
    store.set_setting("project_dir", str(PROJECT_ROOT))
    return store


def test_invoke_skill_resolves_every_skill(brain_store):
    """The `invoke_skill(name)` tool must be able to find every skill
    on disk. A skill that exists but can't be reached is dead code."""
    from dariusai.agent.tools import _invoke_skill

    for path in _all_skills():
        name = path.parent.name
        result = _invoke_skill(brain_store, name)
        assert "no skill named" not in result, (
            f"invoke_skill({name!r}) failed — {result[:120]}"
        )
        assert not result.startswith("ERROR:"), (
            f"invoke_skill({name!r}) returned error — {result[:120]}"
        )


def test_invoke_skill_loads_using_addon_skills_body(brain_store):
    """The bootstrap skill must return its full body on `full=True` —
    the model reads it to learn the auto-trigger rules."""
    from dariusai.agent.tools import _invoke_skill

    result = _invoke_skill(brain_store, "using-addon-skills", full=True)
    assert "EXTREMELY-IMPORTANT" in result
    assert "auto-trigger" in result.lower()
    assert "Library Map" in result


def test_invoke_skill_distils_a_large_skill_by_default(brain_store):
    """Default is distillation, not the whole file: a 12 KB bootstrap
    re-sent on every tool iteration of a 60-iteration turn is the single
    largest avoidable cost in a chat turn. The model gets the section
    list plus the passages matching its query, and asks for more if it
    needs more."""
    from dariusai.agent.tools import _invoke_skill

    full = _invoke_skill(brain_store, "using-addon-skills", full=True)
    distilled = _invoke_skill(brain_store, "using-addon-skills",
                              query="when should I use an addon skill")
    assert len(distilled) < len(full) / 2
    assert "Sections:" in distilled
    assert "full=true" in distilled
