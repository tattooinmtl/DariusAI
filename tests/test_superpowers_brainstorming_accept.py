"""Acceptance test for the superpowers integration.

The superpowers project documents the acceptance test for any new
harness integration in its own `CLAUDE.md` (§ "New Harness Support"):

> The acceptance test. Open a clean session in the new harness and
> send exactly this user message:
>     "Let's make a react todo list"
> A working integration auto-triggers the `brainstorming` skill
> before any code is written.

This test pins the wiring that makes that work in dariusai-harness:

1. The `using-superpowers` bootstrap is the first block of the
   doctrine spliced into every agent's system prompt.
2. The bootstrap explicitly tells the model to invoke
   `brainstorming` before any creative work.
3. The `invoke_skill(name)` tool is registered and resolves to the
   full SKILL.md content for the brainstorming skill.
4. The brainstorming skill body itself contains the HARD-GATE
   directive that says "do NOT write any code until the design has
   been approved", so the model that loads it has no excuse to skip
   the brainstorming step.

The test does not exercise a real LLM (no API key, no network). It
asserts on the components the model sees — the system prompt it
would receive and the tool it would call — so a regression in any of
those four pieces fails here, before the user sees the breakage in
the running app.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# `src/` on the path so the agent modules import under test the same
# way they do in production.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent import doctrine  # noqa: E402 — path setup must come first


# ---------------------------------------------------------------------------
# 1. The bootstrap is in the doctrine and the doctrine is the first thing
#    every agent prompt sees.
# ---------------------------------------------------------------------------


def test_bootstrap_is_in_the_doctrine():
    """The `<EXTREMELY-IMPORTANT>` block and the `using-superpowers`
    skill body must be present in the doctrine that gets spliced into
    every agent's system prompt. If they're missing, the model has no
    way to know it should invoke skills first."""
    assert hasattr(doctrine, "SUPERPOWERS_BOOTSTRAP"), "doctrine must expose the bootstrap as a top-level constant"
    bootstrap = doctrine.SUPERPOWERS_BOOTSTRAP
    assert "<EXTREMELY-IMPORTANT>" in bootstrap, "bootstrap must use the verbatim <EXTREMELY-IMPORTANT> block"
    assert "ABSOLUTELY MUST invoke the skill" in bootstrap, "bootstrap must include the no-rationalization language"
    # The bootstrap also has the rule that names brainstorming as the
    # entry-point for creative work — that is the chain the acceptance
    # prompt exercises.
    assert "brainstorming skill first" in bootstrap, "bootstrap must point brainstorming at creative work"
    # And the rule about Skill priority ("process skills come first").
    assert "process skills come first" in bootstrap, "bootstrap must keep process skills before implementation skills"


def test_with_doctrine_splices_bootstrap_into_chat_system():
    """CHAT_SYSTEM is the role prompt for the long-lived chat
    session. with_doctrine is the splice point. The bootstrap must
    show up in any system prompt produced by with_doctrine so the
    chat agent sees the methodology on every turn."""
    wrapped = doctrine.with_doctrine("YOU ARE A TEST")
    assert "EXTREMELY-IMPORTANT" in wrapped, "every with_doctrine() call must include the bootstrap"
    assert "brainstorming" in wrapped, "the bootstrap must trigger brainstorming on creative tasks"
    # The dariusai-specific rules also come through — these are the
    # bridge from superpowers' generic methodology to this project's
    # specific environment.
    assert "harness: this app" in wrapped, "doctrine must include the dariusai architecture rule"
    assert "search_brain" in wrapped, "doctrine must include the dariusai knowledge-loop rule"


# ---------------------------------------------------------------------------
# 2. The `invoke_skill(name)` tool resolves to the brainstorming skill's
#    full SKILL.md body — that's what the model reads when it follows
#    the bootstrap's directive.
# ---------------------------------------------------------------------------


def test_invoke_skill_resolves_brainstorming_to_full_skill_md(tmp_path, monkeypatch):
    """End-to-end: invoke_skill('brainstorming') must return the full
    SKILL.md content. The test sets up a temp project with a
    `addon/skills/superpowers/brainstorming/SKILL.md` file, points
    the brain's project_dir at it, builds a tool registry, and
    calls the tool."""
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore

    # Build a minimal harness project tree in a temp dir.
    project = tmp_path / "proj"
    skill_dir = project / "addon" / "skills" / "superpowers" / "brainstorming"
    skill_dir.mkdir(parents=True)
    skill_md = (
        "---\n"
        "name: brainstorming\n"
        'description: "You MUST use this before any creative work."\n'
        "---\n\n"
        "# Brainstorming — test fixture\n\n"
        "Do NOT write any code until the user has approved a design.\n"
    )
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    # Stand up a brain rooted at a temp home, with project_dir set to
    # the temp project we just built.
    home = tmp_path / "brain"
    home.mkdir()
    store = BrainStore(home)
    store.set_setting("project_dir", str(project))

    reg = build_tool_registry(store)
    assert "invoke_skill" in reg.specs, "invoke_skill must be registered as a tool"

    result = reg.call("invoke_skill", {"name": "brainstorming"})
    assert "Brainstorming" in result, "invoke_skill must return the SKILL.md body, not a placeholder"
    assert "Do NOT write any code" in result, "the HARD-GATE directive must be in the body the model reads"
    assert "until the user has approved a design" in result, "the gate must be preserved verbatim"

    # Group prefix is also accepted (superpowers docs use 'superpowers:brainstorming').
    prefixed = reg.call("invoke_skill", {"name": "superpowers:brainstorming"})
    assert prefixed == result, "the 'group:skill' prefix must be stripped before lookup"


def test_invoke_skill_returns_helpful_error_when_skill_missing(tmp_path):
    """If the skill name doesn't exist anywhere in the addon tree,
    the tool must say so explicitly — not silently return a stub —
    so the model can fall back to browse_brain() or search_brain()
    rather than hallucinating a skill body."""
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore

    project = tmp_path / "proj"
    # Empty project — no addon tree at all.
    project.mkdir()

    home = tmp_path / "brain"
    home.mkdir()
    store = BrainStore(home)
    store.set_setting("project_dir", str(project))

    reg = build_tool_registry(store)
    result = reg.call("invoke_skill", {"name": "nonexistent-skill"})
    assert "no skill named" in result, "missing-skill response must say so explicitly"
    assert "browse_brain" in result, "missing-skill response must point at browse_brain() as the fallback"


# ---------------------------------------------------------------------------
# 3. The brainstorming skill body itself contains the HARD-GATE directive
#    that prevents the model from skipping the design step.
# ---------------------------------------------------------------------------


def test_brainstorming_skill_md_has_hard_gate_directive():
    """The brainstorming skill body is the canonical instructions the
    model reads when it invokes the skill. The HARD-GATE directive
    inside it is the *only* thing standing between 'the model wrote
    code' and 'the model asked a clarifying question first'. Verify
    the directive is present and unambiguous."""
    skill_path = Path(__file__).resolve().parents[1] / "addon" / "skills" / "superpowers" / "brainstorming" / "SKILL.md"
    assert skill_path.is_file(), f"brainstorming SKILL.md must be present at {skill_path}"
    body = skill_path.read_text(encoding="utf-8")
    assert "<HARD-GATE>" in body, "brainstorming must contain a <HARD-GATE> directive"
    assert "Do NOT" in body, "the gate must be in capitalised directive form"
    assert "design" in body.lower(), "the gate must explicitly reference the design"


# ---------------------------------------------------------------------------
# 4. The full set of 14 superpowers skills is on disk so the model has
#    the methodology to choose from.
# ---------------------------------------------------------------------------


EXPECTED_SKILLS = {
    "brainstorming",
    "dispatching-parallel-agents",
    "executing-plans",
    "finishing-a-development-branch",
    "receiving-code-review",
    "requesting-code-review",
    "subagent-driven-development",
    "systematic-debugging",
    "test-driven-development",
    "using-git-worktrees",
    "using-superpowers",
    "verification-before-completion",
    "writing-plans",
    "writing-skills",
}


def test_all_fourteen_superpowers_skills_are_present():
    """The superpowers methodology is the 14 SKILL.md files in
    `addon/skills/superpowers/<name>/`. If any are missing, the
    agent has fewer skills to choose from — and the bootstrap's
    promise ('all skills in this folder') is broken."""
    base = Path(__file__).resolve().parents[1] / "addon" / "skills" / "superpowers"
    actual = {p.name for p in base.iterdir() if p.is_dir()}
    missing = EXPECTED_SKILLS - actual
    assert not missing, f"missing superpowers skills: {sorted(missing)}"
    extra = actual - EXPECTED_SKILLS
    assert not extra, f"unexpected directories under superpowers/: {sorted(extra)}"
