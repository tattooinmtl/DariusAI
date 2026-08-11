"""The self-improvement loop (project spec, "Self-Improving Learning Loop"):
when the agent can't solve something with its existing tools/knowledge, it
researches the topic, then files what it found as a new skill node.

Split into two steps on purpose:

1. research() — real web search + page fetch, returns raw material (title,
   url, fetched text) for the agent's own LLM reasoning to read. This module
   does NOT pick quotes or write prose; it only gathers.

2. save_learned_skill() — takes the agent's already-synthesized fields
   (problem/solution/code examples/best practices/edge cases) plus the
   sources it cites, enforces the spec's hard requirements (>= 5 sources,
   spread across more than one domain so it's not just one blog's opinion),
   and writes the skill.md via BrainStore.

This mirrors how a tool-calling agent loop actually works: the LLM decides
what a source "says" and picks the quote, but the *rules* (5 sources, real
consensus, structured sections) are enforced in code, not left to the model
choosing to follow instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from ..events.bus import bus
from ..search.web_search import fetch_text, web_search
from .skill import Skill, Source
from .store import COORDINATOR_ID, BrainStore

MIN_SOURCES = 5
MIN_DISTINCT_DOMAINS = 3


class InsufficientResearchError(Exception):
    """Raised when research or sourcing doesn't clear the spec's bar —
    surfaced to the agent so it can search more / broaden the query rather
    than silently filing a thin skill."""


@dataclass
class ResearchItem:
    title: str
    url: str
    snippet: str
    text: str


def research(topic: str, min_results: int = MIN_SOURCES, max_results: int = 8) -> list[ResearchItem]:
    """Search the web for `topic` and fetch each hit's page text. Returns
    whatever it could actually reach — callers decide whether that's enough
    (save_learned_skill enforces the hard minimum at citation time)."""
    hits = web_search(topic, max_results=max_results)
    items = []
    for hit in hits:
        text = fetch_text(hit.url)
        if text.startswith("(failed to fetch"):
            continue
        items.append(ResearchItem(title=hit.title, url=hit.url, snippet=hit.snippet, text=text))
    if len(items) < min_results:
        raise InsufficientResearchError(
            f"only reached {len(items)}/{min_results} sources for {topic!r} — "
            "broaden the query or try a different search term before citing this research."
        )
    return items


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _validate_sources(sources: list[Source]) -> None:
    if len(sources) < MIN_SOURCES:
        raise InsufficientResearchError(
            f"a skill needs at least {MIN_SOURCES} cited sources, got {len(sources)}."
        )
    domains = {_domain(s.url) for s in sources if s.url}
    if len(domains) < MIN_DISTINCT_DOMAINS:
        raise InsufficientResearchError(
            f"sources must span at least {MIN_DISTINCT_DOMAINS} distinct domains "
            f"(consensus check) — got {len(domains)}: {sorted(domains)}."
        )
    for s in sources:
        if not s.quote or not s.quote.strip():
            raise InsufficientResearchError(f"source {s.url} has no quote — cite what it actually said.")


def save_learned_skill(
    store: BrainStore,
    *,
    title: str,
    problem: str,
    solution: str,
    code_examples: str,
    best_practices: str,
    edge_cases: str,
    sources: list[Source] | list[dict],
    category: str = "skill",
    tags: list[str] | None = None,
    related: list[str] | None = None,
    tool_generated: str | None = None,
) -> Skill:
    """Persist a self-taught skill. Raises InsufficientResearchError if the
    citation bar isn't cleared — nothing gets written in that case."""
    parsed_sources = [
        s if isinstance(s, Source) else Source(url=s["url"], quote=s["quote"]) for s in sources
    ]
    _validate_sources(parsed_sources)

    skill = Skill(
        id="",
        title=title,
        category=category,
        tags=tags or [],
        problem=problem,
        solution=solution,
        code_examples=code_examples,
        best_practices=best_practices,
        edge_cases=edge_cases,
        sources=parsed_sources,
        related=related or [],
        tool_generated=tool_generated,
    )
    saved = store.add_skill(skill)
    bus.publish({
        "kind": "skill_learned", "id": saved.id, "route": COORDINATOR_ID,
        "label": saved.title, "source_count": len(saved.sources),
    })
    return saved
