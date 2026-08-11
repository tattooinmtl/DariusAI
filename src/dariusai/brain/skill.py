"""A Skill is one node's content: a structured, cited lesson the agent either
learned itself (via the self-improvement loop in brain/learn.py) or a human
filed by hand through the viz panel. Stored as a single markdown file with
YAML frontmatter — human-readable, diffable, editable in any editor — plus
indexed into BrainStore's SQLite/NetworkX layer for fast lookup.

Section layout is fixed by the project spec (dariusai-harness.md): Problem,
Solution, Code Examples, Best Practices, Edge Cases / Gotchas, Sources,
Related Skills. to_markdown()/from_markdown() must round-trip exactly through
these sections so editing a skill in the viz panel and saving it back doesn't
lose or reshuffle content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yaml

SECTIONS = [
    ("problem", "Problem"),
    ("solution", "Solution"),
    ("code_examples", "Code Examples"),
    ("best_practices", "Best Practices"),
    ("edge_cases", "Edge Cases / Gotchas"),
]


# Every heading this format defines. Used as the section terminator so a
# section's own markdown headings don't end it.
_SECTION_HEADINGS = "|".join(
    re.escape(h) for h in [heading for _, heading in SECTIONS] + ["Sources", "Related Skills"]
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Source:
    url: str
    quote: str

    def to_line(self) -> str:
        safe_quote = self.quote.replace('"', "'").replace("\n", " ").strip()
        return f'- [{self.url}]({self.url}) — "{safe_quote}"'

    @staticmethod
    def from_line(line: str) -> "Source | None":
        m = re.match(r"^-\s*\[(.*?)\]\((.*?)\)\s*—\s*\"(.*)\"\s*$", line.strip())
        if not m:
            return None
        return Source(url=m.group(2), quote=m.group(3))


@dataclass
class Skill:
    id: str
    title: str
    # skill | tool | language | framework | pattern | project | preference —
    # open-ended on purpose (spec: "users can define new categories").
    category: str = "skill"
    tags: list[str] = field(default_factory=list)
    problem: str = ""
    solution: str = ""
    code_examples: str = ""
    best_practices: str = ""
    edge_cases: str = ""
    sources: list[Source] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    tool_generated: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    usage_count: int = 0

    def to_markdown(self) -> str:
        frontmatter = {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "tags": self.tags,
            "related": self.related,
            "tool_generated": self.tool_generated,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "usage_count": self.usage_count,
        }
        fm = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
        sources_md = "\n".join(s.to_line() for s in self.sources) or "(none yet)"
        related_md = ", ".join(self.related) if self.related else "(none)"
        body = [f"# {self.title}", ""]
        for attr, heading in SECTIONS:
            body.append(f"## {heading}")
            body.append(getattr(self, attr) or "_(not filled in)_")
            body.append("")
        body.append("## Sources")
        body.append(sources_md)
        body.append("")
        body.append("## Related Skills")
        body.append(related_md)
        body.append("")
        return f"---\n{fm}\n---\n\n" + "\n".join(body)

    @classmethod
    def from_markdown(cls, raw: str) -> "Skill":
        m = re.match(r"^---\n(.*?)\n---\n\n(.*)$", raw, re.S)
        if not m:
            raise ValueError("not a valid skill.md: missing YAML frontmatter block")
        fm = yaml.safe_load(m.group(1)) or {}
        body = m.group(2)

        def section(heading: str) -> str:
            # Stop at the next *known* section heading, not at any `##`.
            # A skill's content is markdown and routinely contains its own
            # headings — a language guide with "## Phase 1", a checklist with
            # "## Rules". Treating those as section boundaries silently threw
            # away everything after the first one: the file on disk stayed
            # complete, but reading it back returned a truncated skill.
            sm = re.search(
                rf"^## {re.escape(heading)}\s*\n(.*?)(?=\n## (?:{_SECTION_HEADINGS})\s*\n|\Z)",
                body, re.S | re.M,
            )
            if not sm:
                return ""
            text = sm.group(1).strip()
            return "" if text == "_(not filled in)_" else text

        sources = []
        for line in section("Sources").splitlines():
            src = Source.from_line(line)
            if src:
                sources.append(src)

        related_raw = section("Related Skills")
        related = (
            []
            if related_raw in ("", "(none)")
            else [s.strip() for s in related_raw.split(",") if s.strip()]
        )

        kwargs = {attr: section(heading) for attr, heading in SECTIONS}
        return cls(
            id=fm.get("id", ""),
            title=fm.get("title", ""),
            category=fm.get("category", "skill"),
            tags=list(fm.get("tags") or []),
            related=related,
            tool_generated=fm.get("tool_generated"),
            created_at=fm.get("created_at") or _now(),
            updated_at=fm.get("updated_at") or _now(),
            usage_count=int(fm.get("usage_count") or 0),
            sources=sources,
            **kwargs,
        )
