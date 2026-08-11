"""BrainStore — the "central brain index" from the project spec: a
NetworkX graph for traversal/visualization, backed by SQLite for durable
node/edge metadata, with each node's full content living in its own
skill.md file under <home>/skills/<category>/<id>.md (spec decision:
"Files: skill_name.md + SQLite index").

SQLite is the source of truth for the graph shape (nodes/edges/usage);
the in-memory NetworkX graph is rebuilt from it on every write, which is
plenty fast at the node counts a single-user local brain will ever reach
(hundreds to low thousands) and avoids the two ever drifting apart.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from brain_graph import BrainGraph

from . import secrets
from .skill import Skill

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    label TEXT NOT NULL,
    file_path TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    source_count INTEGER NOT NULL DEFAULT 0,
    usage_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'related',
    PRIMARY KEY (source, target, kind)
);
CREATE INDEX IF NOT EXISTS idx_nodes_category ON nodes(category);

-- General key/value store for app + project preferences (last project dir,
-- window layout overrides that need to live server-side, etc.)
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- LLM provider configs. api_key_encrypted is a DPAPI-protected blob (see
-- brain/secrets.py) — never stored or returned as plaintext; the decrypted
-- key only ever exists in memory, only when actually constructing a client.
CREATE TABLE IF NOT EXISTS providers (
    name TEXT PRIMARY KEY,
    base_url TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    api_key_encrypted BLOB NOT NULL DEFAULT x'',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Full-text index over what a skill actually *says*, not just what it is called.
#
# Matching on label and tags alone left the brain unable to answer for its own
# content: "http" returned nothing while 42 skills discussed it, because tags are
# derived from the folder name and nothing else. Titles are a table of contents,
# not an index.
#
# Deliberately NOT over the providers table — only skill text is indexed, so an
# encrypted API key can never surface through a search.
#
# `content=''` makes this a contentless index: FTS stores the terms, the bodies
# stay in their markdown files, and there is no second copy of every skill to
# drift out of sync.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id UNINDEXED, label, tags, problem, solution, tokenize='porter unicode61'
);
"""

COORDINATOR_ID = "brain-coordinator"
# The trunk that conversation atoms branch from, so recorded turns form a
# limb of the brain instead of yet more spokes around the centre.
CONVERSATIONS_ROOT = "conversations"


class BrainStore:
    def __init__(self, home: Path | str):
        self.home = Path(home)
        self.skills_dir = self.home / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "brain.db"
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.fts_enabled = self._init_fts()
        self.graph = BrainGraph()
        self._load_graph()

    # ---- internal -----------------------------------------------------

    def _init_fts(self) -> bool:
        """Create the full-text index, and backfill it for a brain that predates
        it. Returns False on a SQLite built without FTS5, in which case search
        falls back to the old LIKE match — degraded, but never broken."""
        try:
            self.conn.executescript(FTS_SCHEMA)
            self.conn.commit()
        except sqlite3.OperationalError:
            return False

        empty = self.conn.execute("SELECT COUNT(*) FROM nodes_fts").fetchone()[0] == 0
        has_nodes = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] > 0
        if empty and has_nodes:
            self._reindex_all()
        return True

    def _reindex_all(self) -> None:
        """Rebuild the index from the skill files on disk. Cheap at the node
        counts a local brain reaches, and it means a corrupt or missing index is
        always recoverable by deleting the table."""
        self.conn.execute("DELETE FROM nodes_fts")
        rows = self.conn.execute("SELECT id, label, tags FROM nodes").fetchall()
        for node_id, label, tags in rows:
            try:
                self._index_skill(self.get_skill(node_id))
            except (KeyError, FileNotFoundError):
                # An index row whose markdown file is gone — older brains carry
                # these. Index what the row itself knows rather than dropping the
                # node from search entirely: it was findable by label and tags
                # before this index existed, and it must not become less findable
                # because of it.
                self.conn.execute(
                    "INSERT INTO nodes_fts (id, label, tags, problem, solution) "
                    "VALUES (?, ?, ?, '', '')",
                    (node_id, label or "", " ".join(json.loads(tags or "[]"))),
                )
        self.conn.commit()

    def _index_skill(self, skill: Skill) -> None:
        if not getattr(self, "fts_enabled", False):
            return
        self.conn.execute("DELETE FROM nodes_fts WHERE id = ?", (skill.id,))
        self.conn.execute(
            "INSERT INTO nodes_fts (id, label, tags, problem, solution) VALUES (?, ?, ?, ?, ?)",
            (skill.id, skill.title, " ".join(skill.tags), skill.problem, skill.solution),
        )

    def _load_graph(self) -> None:
        node_rows = []
        rows = self.conn.execute(
            "SELECT id, category, label, file_path, tags, source_count, "
            "usage_count, created_at, updated_at FROM nodes"
        ).fetchall()
        for row in rows:
            node_rows.append({
                "id": row[0],
                "category": row[1],
                "label": row[2],
                "file_path": row[3],
                "tags": row[4],
                "source_count": row[5],
                "usage_count": row[6],
                "created_at": row[7],
                "updated_at": row[8],
            })

        edge_rows = list(self.conn.execute("SELECT source, target, kind FROM edges"))
        self.graph.load_from_rows(node_rows, edge_rows)

    # ---- writes ---------------------------------------------------------

    def add_skill(self, skill: Skill) -> Skill:
        """Insert or overwrite a skill's file + index row. Same path for a
        brand-new self-taught skill and a manual edit from the viz panel."""
        if not skill.id:
            skill.id = f"{skill.category}-{uuid.uuid4().hex[:10]}"
        skill.updated_at = skill.updated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        subdir = self.skills_dir / skill.category
        subdir.mkdir(parents=True, exist_ok=True)
        path = subdir / f"{skill.id}.md"
        path.write_text(skill.to_markdown(), encoding="utf-8")
        rel_path = str(path.relative_to(self.home))

        self.conn.execute(
            "INSERT INTO nodes (id, category, label, file_path, tags, source_count, "
            "usage_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET category=excluded.category, label=excluded.label, "
            "file_path=excluded.file_path, tags=excluded.tags, source_count=excluded.source_count, "
            "updated_at=excluded.updated_at",
            (
                skill.id, skill.category, skill.title, rel_path, json.dumps(skill.tags),
                len(skill.sources), skill.usage_count, skill.created_at, skill.updated_at,
            ),
        )
        for related_id in skill.related:
            self.conn.execute(
                "INSERT OR IGNORE INTO edges (source, target, kind) VALUES (?, ?, 'related')",
                (skill.id, related_id),
            )
        self._index_skill(skill)
        self.conn.commit()
        self._load_graph()
        return skill

    def ensure_branch(self, node_id: str, title: str, category: str, description: str = "") -> str:
        """Create a trunk node if it isn't there yet, and return its id.

        Idempotent: a branch is created once and then simply linked to, so
        every conversation after the first attaches to the same limb instead
        of spawning a new one.
        """
        try:
            self.get_skill(node_id)
            return node_id
        except (KeyError, FileNotFoundError):
            pass
        from .skill import Skill
        self.add_skill(Skill(
            id=node_id,
            title=title,
            category=category,
            tags=[category, "branch"],
            problem=description,
            solution="Conversations recorded from chat attach to this branch.",
        ))
        return node_id

    def delete(self, node_id: str) -> None:
        row = self.conn.execute("SELECT file_path FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row:
            (self.home / row[0]).unlink(missing_ok=True)
        self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        self.conn.execute("DELETE FROM edges WHERE source = ? OR target = ?", (node_id, node_id))
        self.conn.commit()
        self._load_graph()

    def touch_usage(self, node_id: str) -> None:
        self.conn.execute("UPDATE nodes SET usage_count = usage_count + 1 WHERE id = ?", (node_id,))
        self.conn.commit()
        self._load_graph()

    # ---- reads ------------------------------------------------------------

    def get_skill(self, node_id: str) -> Skill:
        row = self.conn.execute("SELECT file_path FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            raise KeyError(f"no node with id {node_id!r}")
        raw = (self.home / row[0]).read_text(encoding="utf-8")
        return Skill.from_markdown(raw)

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Find skills by what they say, not only by what they are called.

        Returns ids and labels only — never bodies. That is the whole point of
        search-then-load: the agent sees a shortlist cheaply and pays for a full
        body only via `load_skill`, on the one it actually wants.
        """
        rows = self._search_fts(query, limit) if self.fts_enabled else []
        if not rows:
            rows = self._search_like(query, limit)
        return [
            {"id": r[0], "category": r[1], "label": r[2], "tags": json.loads(r[3]), "usage_count": r[4]}
            for r in rows
        ]

    def _search_fts(self, query: str, limit: int) -> list[tuple]:
        # Relevance first (bm25 weights the title and the description above the
        # body, so a skill *about* the topic outranks one that merely mentions
        # it), then usage. Ranking by usage alone was arbitrary on a fresh
        # install, where every skill has usage_count = 0.
        try:
            return self.conn.execute(
                "SELECT n.id, n.category, n.label, n.tags, n.usage_count "
                "FROM nodes_fts f JOIN nodes n ON n.id = f.id "
                "WHERE nodes_fts MATCH ? "
                "ORDER BY bm25(nodes_fts, 0.0, 10.0, 5.0, 8.0, 1.0), n.usage_count DESC "
                "LIMIT ?",
                (self._fts_query(query), limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Malformed MATCH expression (stray quote, bare operator, …). A bad
            # query should return nothing, not take the caller down.
            return []

    @staticmethod
    def _fts_query(query: str) -> str:
        """Turn free text into a safe FTS expression.

        Every term is quoted, so punctuation an agent naturally types — `three.js`,
        `C++`, `async/await` — is matched literally instead of being read as FTS
        syntax. Terms are AND-ed, and a trailing `*` makes the last one a prefix
        so partial words still hit.
        """
        terms = [t.replace('"', "") for t in query.split()]
        terms = [t for t in terms if t]
        if not terms:
            return '""'
        quoted = [f'"{t}"' for t in terms[:-1]] + [f'"{terms[-1]}"*']
        return " AND ".join(quoted)

    def _search_like(self, query: str, limit: int) -> list[tuple]:
        q = f"%{query.lower()}%"
        return self.conn.execute(
            "SELECT id, category, label, tags, usage_count FROM nodes "
            "WHERE lower(label) LIKE ? OR lower(tags) LIKE ? "
            "ORDER BY usage_count DESC, updated_at DESC LIMIT ?",
            (q, q, limit),
        ).fetchall()

    def to_graph_payload(self) -> dict[str, Any]:
        return self.graph.to_payload()

    # ---- settings (key/value preferences) --------------------------------

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def all_settings(self) -> dict[str, str]:
        return dict(self.conn.execute("SELECT key, value FROM settings").fetchall())

    # ---- LLM providers (API keys encrypted at rest via DPAPI) -------------

    def upsert_provider(
        self, name: str, base_url: str = "", model: str = "", api_key: str | None = None
    ) -> dict[str, Any]:
        """api_key=None leaves an existing key untouched (so editing base_url
        alone doesn't require re-entering the key); api_key="" clears it."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if api_key is not None:
            encrypted = secrets.encrypt(api_key)
            self.conn.execute(
                "INSERT INTO providers (name, base_url, model, api_key_encrypted, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET base_url=excluded.base_url, model=excluded.model, "
                "api_key_encrypted=excluded.api_key_encrypted, updated_at=excluded.updated_at",
                (name, base_url, model, encrypted, now, now),
            )
        else:
            self.conn.execute(
                "INSERT INTO providers (name, base_url, model, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET base_url=excluded.base_url, model=excluded.model, "
                "updated_at=excluded.updated_at",
                (name, base_url, model, now, now),
            )
        self.conn.commit()
        return self.get_provider(name)

    def list_providers(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT name, base_url, model, api_key_encrypted, is_active FROM providers ORDER BY name"
        ).fetchall()
        return [self._provider_row_to_dict(r) for r in rows]

    def get_provider(self, name: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT name, base_url, model, api_key_encrypted, is_active FROM providers WHERE name = ?", (name,)
        ).fetchone()
        return self._provider_row_to_dict(row) if row else None

    @staticmethod
    def _provider_row_to_dict(row) -> dict[str, Any]:
        name, base_url, model, encrypted, is_active = row
        has_key = bool(encrypted)
        masked = secrets.mask(secrets.decrypt(encrypted)) if has_key else ""
        return {
            "name": name, "base_url": base_url, "model": model,
            "has_api_key": has_key, "api_key_masked": masked, "is_active": bool(is_active),
        }

    def get_provider_api_key(self, name: str) -> str:
        """The one place a plaintext key comes back — for constructing an
        LLM client, never for an HTTP response."""
        row = self.conn.execute("SELECT api_key_encrypted FROM providers WHERE name = ?", (name,)).fetchone()
        if not row or not row[0]:
            raise KeyError(f"no API key stored for provider {name!r}")
        return secrets.decrypt(row[0])

    def delete_provider(self, name: str) -> None:
        self.conn.execute("DELETE FROM providers WHERE name = ?", (name,))
        self.conn.commit()

    def set_active_provider(self, name: str) -> None:
        if not self.get_provider(name):
            raise KeyError(f"no provider named {name!r}")
        self.conn.execute("UPDATE providers SET is_active = (name = ?)", (name,))
        self.conn.commit()

    def get_active_provider(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT name, base_url, model, api_key_encrypted, is_active FROM providers WHERE is_active = 1"
        ).fetchone()
        return self._provider_row_to_dict(row) if row else None
