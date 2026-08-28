"""Passage-level retrieval over skill markdown — the "distill, don't load"
half of the agent loop.

The brain's node index (store.py) answers *which* skill is relevant; it
returns ids and labels only, and the body is then paid for in full by
`load_skill`/`invoke_skill`. That was fine when a skill was a page. It
isn't any more: the library's median SKILL.md is ~7 KB and the largest is
over 100 KB, and every byte pulled into the conversation is re-sent to the
provider on *every* subsequent tool iteration of the turn. One large skill
read early in a 60-iteration turn is paid for sixty times.

This module indexes each skill file as a list of small passages (a heading
plus the paragraphs under it) in a second FTS5 table, so a lookup can return
the two or three paragraphs that actually answer the question instead of the
whole document. Same storage story as the rest of the brain: SQLite in
`brain.db`, markdown on disk as the source of truth, index rebuildable by
deleting the table.

Passage text lives inside the FTS table rather than in a side table: a
lookup has to *return* the prose, so there is exactly one copy either way,
and a single table means one delete path when a file changes.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# One passage is aimed at a paragraph or two — big enough to stand on its
# own when the model reads it cold, small enough that four of them are a
# rounding error next to a full SKILL.md.
TARGET_CHARS = 700
MAX_CHARS = 1400

# What a single lookup is allowed to put into the conversation. Four
# passages at ~700 chars is ~500 tokens; a median SKILL.md is ~1,800 and
# the big ones are ~26,000.
DEFAULT_TOP_K = 4
DEFAULT_BUDGET_CHARS = 2400

PASSAGE_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    source UNINDEXED, path UNINDEXED, ord UNINDEXED, heading, body,
    tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS passage_files (
    source TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    chunks INTEGER NOT NULL DEFAULT 0,
    indexed_at REAL NOT NULL DEFAULT 0
);
"""

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(?:```|~~~)")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class Passage:
    source: str
    path: str
    heading: str
    body: str
    score: float = 0.0

    def render(self) -> str:
        head = self.source
        if self.heading:
            head += f" > {self.heading}"
        return f"[{head}]\n{self.body}"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def strip_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a SKILL.md into its frontmatter fields and its body.

    Deliberately not YAML-parsed: frontmatter in this library is flat
    `key: value` lines, some of them with unquoted colons inside the
    value, and a strict parser turns one malformed skill into a failed
    index rather than a slightly worse chunk.
    """
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    fields: dict[str, str] = {}
    key = ""
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if km:
            key = km.group(1).lower()
            fields[key] = km.group(2).strip()
        elif key and line.strip():
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields, text[m.end():]


def chunk_markdown(text: str, target_chars: int = TARGET_CHARS,
                   max_chars: int = MAX_CHARS) -> list[tuple[str, str]]:
    """Split markdown into `(heading, body)` passages.

    Boundaries are headings first, then blank lines once a passage is past
    `target_chars`. Fenced code blocks are never split — half a block is
    worse than none, and the fences are what tell the model it is code.
    """
    fields, body = strip_frontmatter(text)
    out: list[tuple[str, str]] = []

    # The frontmatter description is the single most useful passage in the
    # file: it is what the auto-trigger tables are written against, so a
    # lookup for "when do I use X" should hit it.
    lead = " ".join(fields.get(k, "") for k in ("name", "description")).strip()
    if lead:
        out.append(("about", lead[:max_chars]))

    trail: list[str] = []
    heading = ""
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal buf
        chunk = "\n".join(buf).strip()
        buf = []
        if not chunk:
            return
        for piece in _hard_split(chunk, max_chars):
            out.append((heading, piece))

    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            buf.append(line)
            continue
        if not in_fence:
            hm = _HEADING.match(line)
            if hm:
                flush()
                level = len(hm.group(1))
                trail = trail[: level - 1]
                while len(trail) < level - 1:
                    trail.append("")
                trail.append(hm.group(2).strip())
                heading = " > ".join(t for t in trail if t)
                continue
            if not line.strip() and len("\n".join(buf)) >= target_chars:
                flush()
                continue
        buf.append(line)
    flush()
    return [(h, b) for h, b in out if b.strip()]


def _hard_split(chunk: str, max_chars: int) -> list[str]:
    """Break an oversized passage at line boundaries. Only reached by
    wall-of-text sections and long code blocks; splitting mid-line would
    corrupt both."""
    if len(chunk) <= max_chars:
        return [chunk]
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for line in chunk.splitlines():
        if size and size + len(line) + 1 > max_chars:
            pieces.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        pieces.append("\n".join(current))
    return pieces


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


def fts_query(query: str) -> str:
    """Free text into a safe FTS5 MATCH expression.

    Terms are OR-ed here, unlike the node index's AND: a passage lookup is
    a recall problem (find the paragraph that talks about this) rather than
    a filter, and AND-ing a five-word natural question against
    paragraph-sized documents returns nothing far too often. bm25 does the
    discrimination.
    """
    terms = [re.sub(r'["*]', "", t) for t in re.split(r"[^\w.+#-]+", query.lower())]
    terms = [t for t in terms if len(t) > 1][:12]
    if not terms:
        return ""
    return " OR ".join(f'"{t}"*' for t in terms)


class PassageIndex:
    """The RAG side of the brain. Built on the store's own connection so
    passages live in `brain.db` next to the node index and inherit its
    lifecycle (one file to back up, one file to delete for a rebuild)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.enabled = self._init_schema()
        self._last_sync = 0.0

    def _init_schema(self) -> bool:
        try:
            self.conn.executescript(PASSAGE_SCHEMA)
            self.conn.commit()
            return True
        except sqlite3.OperationalError:
            # SQLite built without FTS5. Callers fall back to the old
            # load-it-whole path — more expensive, never broken.
            return False

    # ---- writes ----------------------------------------------------------

    def index_text(self, source: str, path: str, text: str, commit: bool = True) -> int:
        """(Re)index one document. Returns the passage count.

        `commit=False` lets a bulk sync pay for one transaction instead of
        one per file — the difference between a first-run index of the
        whole library taking half a minute and taking a couple of seconds.
        """
        if not self.enabled:
            return 0
        chunks = chunk_markdown(text)
        self.conn.execute("DELETE FROM passages_fts WHERE source = ?", (source,))
        self.conn.executemany(
            "INSERT INTO passages_fts (source, path, ord, heading, body) VALUES (?, ?, ?, ?, ?)",
            [(source, path, i, h, b) for i, (h, b) in enumerate(chunks)],
        )
        self.conn.execute(
            "INSERT INTO passage_files (source, path, fingerprint, chunks, indexed_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET path=excluded.path, "
            "fingerprint=excluded.fingerprint, chunks=excluded.chunks, indexed_at=excluded.indexed_at",
            (source, path, _fingerprint(text), len(chunks), time.time()),
        )
        if commit:
            self.conn.commit()
        return len(chunks)

    def sync_file(self, path: Path, source: str = "", commit: bool = True) -> int:
        """Index a file if its contents changed since last time. Returns the
        passage count, or 0 if it was already current (the common case — this
        runs on every lookup)."""
        if not self.enabled:
            return 0
        source = source or path.parent.name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        row = self.conn.execute(
            "SELECT fingerprint FROM passage_files WHERE source = ?", (source,)
        ).fetchone()
        if row and row[0] == _fingerprint(text):
            return 0
        return self.index_text(source, str(path), text, commit=commit)

    def sync_paths(self, paths: Iterable[Path]) -> int:
        """Bring a set of files up to date. Returns how many were reindexed."""
        changed = 0
        for path in paths:
            if self.sync_file(path, commit=False):
                changed += 1
        if changed:
            self.conn.commit()
        self._last_sync = time.time()
        return changed

    def drop(self, source: str) -> None:
        if not self.enabled:
            return
        self.conn.execute("DELETE FROM passages_fts WHERE source = ?", (source,))
        self.conn.execute("DELETE FROM passage_files WHERE source = ?", (source,))
        self.conn.commit()

    def stale(self, min_interval: float = 30.0) -> bool:
        """True when it is worth re-walking the skill tree. Stops a
        60-iteration turn from stat-ing 200 files sixty times over."""
        return (time.time() - self._last_sync) > min_interval

    # ---- reads -----------------------------------------------------------

    def count(self) -> int:
        if not self.enabled:
            return 0
        return self.conn.execute("SELECT COUNT(*) FROM passages_fts").fetchone()[0]

    def search(self, query: str, k: int = DEFAULT_TOP_K,
               budget_chars: int = DEFAULT_BUDGET_CHARS, source: str = "") -> list[Passage]:
        """Top passages for a query, capped by both count and total characters.

        The character cap is the point of the whole module: whatever the
        query, the caller knows the reply cannot blow up its context.
        """
        if not self.enabled:
            return []
        match = fts_query(query)
        if not match:
            return []
        sql = (
            "SELECT source, path, heading, body, "
            "bm25(passages_fts, 0.0, 0.0, 0.0, 4.0, 1.0) AS rank "
            "FROM passages_fts WHERE passages_fts MATCH ?"
        )
        params: list[object] = [match]
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY rank LIMIT ?"
        params.append(max(k * 3, k))
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # Malformed MATCH expression — a bad query returns nothing
            # rather than taking the turn down.
            return []

        out: list[Passage] = []
        spent = 0
        for src, path, heading, body, rank in rows:
            if len(out) >= k:
                break
            if out and spent + len(body) > budget_chars:
                continue
            out.append(Passage(source=src, path=path, heading=heading or "",
                               body=body, score=-float(rank)))
            spent += len(body)
        return out

    def outline(self, source: str, limit: int = 40) -> list[str]:
        """The document's headings, in order — a table of contents so the
        model can ask for the one section it wants instead of the file."""
        if not self.enabled:
            return []
        rows = self.conn.execute(
            "SELECT heading, MIN(ord) AS first_ord FROM passages_fts WHERE source = ? "
            "GROUP BY heading ORDER BY first_ord LIMIT ?",
            (source, limit),
        ).fetchall()
        return [r[0] for r in rows if r[0] and r[0] != "about"]


def _fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
