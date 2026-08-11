"""Import omni's skill library into the brain.

omni keeps skills as `skills/<name>/SKILL.md` — YAML frontmatter (name,
command, description) over a markdown body. This brain keeps them as Skill
nodes: an indexed row, a file on disk, and a node in the graph. Same idea,
different container, so importing is a translation rather than a rewrite —
nothing about either format changes.

The mapping that matters:

    frontmatter.description -> problem    (what the skill is *for*, which is
                                           what a search has to match on)
    the markdown body       -> solution   (the actual instructions)
    frontmatter.command     -> a tag      (so "/python" finds it)

The body is deliberately kept whole and unsummarised. A skill's value is in
its specifics — the exact commands, the version numbers, the gotchas — and
those are precisely what a summary drops. It stays out of the model's
context until asked for: search returns labels, `load_skill` fetches the
body. That's the deferred-loading pattern, applied to knowledge instead of
tools.

Re-running the import is safe: ids are derived from the skill name, so an
import overwrites its own previous rows instead of duplicating them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from .skill import Skill
from .store import BrainStore

DEFAULT_OMNI_SKILLS = Path.home() / ".omni" / "skills"
ID_PREFIX = "omni-"

# Everything ending in -coding is a language guide. The rest are hand-placed:
# guessing a category from a name is exactly the kind of cleverness that
# silently mis-files things.
CATEGORY_OVERRIDES = {
    "code-review": "tool",
    "extend-omni": "tool",
    "find-skills": "tool",
    "project-doctor": "tool",
    "vs-project-starter": "tool",
    "brandkit": "pattern",
    "design-taste-frontend": "pattern",
    "high-end-visual-design": "pattern",
    "html-game-builder": "pattern",
    "redesign-existing-projects": "pattern",
}


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Minimal frontmatter reader — omni's files are flat `key: value`, so
    pulling in a YAML parser for this would be a dependency bought for
    nothing."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    meta: dict[str, str] = {}
    for line in head.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip("'\"")
    return meta, body.lstrip("\n")


def _title_for(name: str, body: str) -> str:
    heading = re.search(r"^#\s+(.+)$", body, re.M)
    if heading:
        return heading.group(1).strip()
    return name.replace("-", " ").replace("_", " ").title()


def category_for(name: str) -> str:
    if name in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[name]
    if name.endswith("-coding"):
        return "language"
    return "skill"


def parse_skill_file(path: Path) -> Skill | None:
    """One SKILL.md -> one Skill node. Returns None for a file with no
    usable name, rather than inventing one."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _split_frontmatter(text)
    name = meta.get("name") or path.parent.name
    if not name:
        return None

    tags = [part for part in re.split(r"[-_]", name) if part]
    command = meta.get("command", "").strip()
    if command:
        tags.append(command)
    tags.append("omni")

    return Skill(
        id=ID_PREFIX + name,
        title=_title_for(name, body),
        category=category_for(name),
        tags=sorted(set(tags)),
        problem=meta.get("description", "").strip(),
        solution=body.strip(),
        tool_generated="omni-import",
    )


def iter_skill_files(root: Path) -> Iterator[Path]:
    """Any depth. omni keeps skills at `skills/<name>/SKILL.md`; the addon
    tree groups them as `skills/<category>/<name>/SKILL.md`. Walking
    recursively handles both without the caller having to know which."""
    yield from sorted(root.rglob("SKILL.md"))


def group_of(path: Path, root: Path) -> str | None:
    """The folder a skill is grouped under, when the tree has grouping.

    `<root>/<group>/<name>/SKILL.md` -> "<group>"; a flat
    `<root>/<name>/SKILL.md` has no group and returns None.
    """
    rel = path.parent.relative_to(root)
    return rel.parts[0] if len(rel.parts) > 1 else None


DEFAULT_ADDON_DIR = Path(__file__).resolve().parents[3] / "addon"
HOOKS_ROOT = "hooks"

# The OKF knowledge anchor. Conversations branch from it (see agent/doctrine.py),
# which makes it a trunk like `hooks`, not an ordinary skill — so it stays at the
# top level instead of being filed under a group. Named here rather than derived
# at the call site so the doctrine, the import and the tests all agree on one
# spelling; a mismatch here silently stops conversations branching from OKF at
# all, which is invisible until the graph is inspected.
OKF_ANCHOR_ID = "addon-okf-knowledge"

# Trunks that may legitimately touch the coordinator. Everything else must hang
# off a group branch.
TRUNK_IDS = frozenset({HOOKS_ROOT, OKF_ANCHOR_ID})


def import_addon(store: BrainStore, root: Path | str | None = None) -> dict[str, Any]:
    """Import the addon tree: grouped skills, plus the shell hooks.

    Structure matters here. Each top-level group (agent-orchestration,
    research-and-web, …) becomes a branch node, and its skills hang off it,
    so the graph shows the library's own shape instead of 48 more spokes
    around the centre. Hooks get their own branch — they aren't knowledge,
    they're guards that run before a command does, and conflating the two
    would make the graph lie about what it holds.
    """
    root = Path(root) if root else DEFAULT_ADDON_DIR
    if not root.is_dir():
        raise FileNotFoundError(f"no addon directory at {root}")

    imported: list[dict[str, Any]] = []
    branches: set[str] = set()
    superseded: list[tuple[str, str]] = []

    skills_dir = root / "skills"
    if skills_dir.is_dir():
        for path in iter_skill_files(skills_dir):
            skill = parse_skill_file(path)
            if skill is None:
                continue
            group = group_of(path, skills_dir)
            skill.id = "addon-" + path.parent.name
            if group:
                branch_id = "addon-group-" + group
                if branch_id not in branches:
                    store.ensure_branch(branch_id, group.replace("-", " ").title(), "skillset",
                                        f"Addon skill group: {group}")
                    branches.add(branch_id)
                skill.category = group          # its own colour in the viz
                skill.related = [branch_id]
            # `omni` comes from parse_skill_file, which serves the real omni
            # import too. These are addon skills, so the tag is simply wrong
            # here — and a tag carried by all 99 nodes discriminates nothing.
            tags = [t for t in skill.tags if t != "omni"]
            skill.tags = sorted(set(tags + ["addon"] + ([group] if group else [])))
            skill.tool_generated = "addon-import"
            store.add_skill(skill)

            # `superseded_by: <folder>` in the frontmatter becomes a real edge.
            # Supersession written only in prose helps an agent that already
            # opened the wrong file; as an edge it is a fact retrieval can act
            # on before loading anything.
            meta, _ = _split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            replacement = meta.get("superseded_by", "").strip()
            if replacement:
                superseded.append((skill.id, "addon-" + replacement))

            imported.append({"id": skill.id, "title": skill.title, "category": skill.category})

    # Written after every skill exists, so a superseded_by never points at a
    # node that hasn't been imported yet (the graph drops dangling edges).
    for source_id, target_id in superseded:
        store.conn.execute(
            "INSERT OR IGNORE INTO edges (source, target, kind) VALUES (?, ?, 'superseded_by')",
            (source_id, target_id),
        )
    if superseded:
        store.conn.commit()

    hooks_dir = root / "hooks"
    hooks: list[dict[str, Any]] = []
    if hooks_dir.is_dir():
        hook_files = sorted(p for p in hooks_dir.iterdir() if p.is_file())
        if hook_files:
            store.ensure_branch(HOOKS_ROOT, "Hooks", "hook",
                                "Guards that run before the agent's commands do.")
        for path in hook_files:
            body = path.read_text(encoding="utf-8", errors="replace")
            # The leading comment block is a hook's own description of itself.
            summary = "\n".join(
                line.lstrip("#").strip()
                for line in body.splitlines()[:14]
                if line.startswith("#") and not line.startswith("#!")
            ).strip()
            hook = Skill(
                id="hook-" + path.stem,
                title=path.name,
                category="hook",
                tags=sorted({"hook", "addon", path.suffix.lstrip(".") or "file"}),
                problem=summary or f"Hook script {path.name}",
                solution=body,
                related=[HOOKS_ROOT],
                tool_generated="addon-import",
            )
            store.add_skill(hook)
            hooks.append({"id": hook.id, "title": hook.title})

    by_category: dict[str, int] = {}
    for row in imported:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1

    return {
        "source": str(root),
        "imported": len(imported),
        "hooks": len(hooks),
        "groups": sorted(branches),
        "by_category": by_category,
        "skills": imported,
        "hook_nodes": hooks,
        "superseded": [{"id": s, "superseded_by": t} for s, t in superseded],
    }


def import_skills(store: BrainStore, root: Path | str = DEFAULT_OMNI_SKILLS) -> dict[str, Any]:
    """Import every skill under `root`. Returns a summary the CLI and the
    Settings panel can both report from."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"no skills directory at {root}")

    imported, skipped = [], []
    for path in iter_skill_files(root):
        skill = parse_skill_file(path)
        if skill is None:
            skipped.append(str(path))
            continue
        store.add_skill(skill)
        imported.append({"id": skill.id, "title": skill.title, "category": skill.category})

    by_category: dict[str, int] = {}
    for row in imported:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1

    return {
        "source": str(root),
        "imported": len(imported),
        "skipped": len(skipped),
        "by_category": by_category,
        "skills": imported,
    }
