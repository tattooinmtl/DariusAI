"""Web search for the learning loop: DuckDuckGo plus the private GWN gateway.

DuckDuckGo needs no key. The gateway in front of SearXNG
(search.globalwarningnetworks.com) needs the key already issued for this
machine. A missing key or a gateway error leaves DuckDuckGo's results in
place, so research still runs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from ddgs import DDGS

_TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

GATEWAY_URL = "https://search.globalwarningnetworks.com"


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def _gateway_key() -> str:
    """The key for this machine's Grok/Darius slot. Never logged."""
    env = os.environ.get("SEARCH_GWN_API_KEY", "").strip()
    if env:
        return env
    home = Path.home() / ".search-gwn"
    named = os.environ.get("SEARCH_GWN_KEY_FILE", "").strip()
    candidates = [Path(named)] if named else []
    candidates.extend((home / "grok.key", home / "key"))
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return ""


def _duckduckgo(query: str, max_results: int) -> list[SearchResult]:
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


def _gateway(
    query: str,
    max_results: int,
    http_get: Callable[..., Any] | None = None,
) -> list[SearchResult]:
    key = _gateway_key()
    if not key:
        return []
    base = os.environ.get("SEARCH_GWN_URL", GATEWAY_URL).rstrip("/")
    getter = http_get or httpx.get
    response = getter(
        f"{base}/search",
        params={"q": query, "category": "general", "lang": "en", "limit": max_results},
        headers={"Authorization": f"Bearer {key}", "User-Agent": "dariusai-harness"},
        timeout=25.0,
    )
    if getattr(response, "status_code", 200) != 200:
        return []
    payload = response.json()
    out = []
    for row in payload.get("results") or []:
        url = row.get("url") or ""
        if not url:
            continue
        out.append(SearchResult(
            title=row.get("title") or "",
            url=url,
            snippet=row.get("snippet") or "",
        ))
    return out


def _merge(left: list[SearchResult], right: list[SearchResult], limit: int) -> list[SearchResult]:
    """Interleave the two engines and drop a URL the other one already gave."""
    seen: set[str] = set()
    merged: list[SearchResult] = []
    for pair in zip_longest(left, right):
        for item in pair:
            if item is None or not item.url:
                continue
            key = urlparse(item.url)._replace(fragment="").geturl().rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


def web_search(
    query: str,
    max_results: int = 8,
    *,
    ddg: Callable[[str, int], list[SearchResult]] | None = None,
    gateway: Callable[[str, int], list[SearchResult]] | None = None,
) -> list[SearchResult]:
    """DuckDuckGo and the VPS gateway. Either one failing still returns the other."""
    duck = ddg or _duckduckgo
    gate = gateway or (lambda q, n: _gateway(q, n))
    left: list[SearchResult] = []
    right: list[SearchResult] = []
    try:
        left = list(duck(query, max_results) or [])
    except Exception:
        left = []
    try:
        right = list(gate(query, max_results) or [])
    except Exception:
        right = []
    return _merge(left, right, max_results)


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
