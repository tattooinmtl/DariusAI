"""Real web search + page fetch, no API key required — the "auto WebSearch"
half of the self-improvement loop (brain/learn.py drives this: search, then
gather >= 5 sources with a real quote from each before a skill.md can be
written). Uses DuckDuckGo (via the `ddgs` package) since it needs nothing
from the user to start working.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from ddgs import DDGS

_TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def web_search(query: str, max_results: int = 8) -> list[SearchResult]:
    with DDGS() as ddgs:
        hits = ddgs.text(query, max_results=max_results)
    return [
        SearchResult(
            title=h.get("title", ""),
            url=h.get("href") or h.get("url", ""),
            snippet=h.get("body", ""),
        )
        for h in hits
        if h.get("href") or h.get("url")
    ]


def fetch_text(url: str, max_chars: int = 4000, timeout: float = 10.0) -> str:
    """Best-effort plain-text extraction of a page — good enough to pull a
    real, checkable quote from, not a full readability parse."""
    try:
        resp = httpx.get(
            url, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; dariusai-harness/0.1)"},
        )
        resp.raise_for_status()
    except Exception as exc:  # network/DNS/4xx/5xx — surfaced to the caller, not swallowed
        return f"(failed to fetch {url}: {exc})"
    text = _TAG_RE.sub(" ", resp.text)
    text = _ANY_TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars]
