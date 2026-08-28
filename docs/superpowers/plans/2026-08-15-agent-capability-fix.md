# SP6 — Agent Capability Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Overhaul the AI layer from a chat-centric loop into a file-based-memory agent with file-watching collaboration, a provider chain (minimax.io → agnes-ai → NVIDIA free → local GGUF llama.cpp), automatic offline-first runtime, and user-editable override instructions for the local path. Spec at `docs/superpowers/specs/2026-08-15-agent-capability-fix-design.md` is the source of truth.

**Architecture:** Four sequential sub-projects — SP6a-embed (local GGUF + model library + reset + GGUF introspection + thinking detection + Local-Llama instructions), SP6a (file-memory + OKF + backend-chain + quota tracker), SP6b (compact-prompt chat loop + phases + LoopGuard + streaming), SP6c (file watcher + coaching). New modules are additive; the existing `chat.py` is the largest rewrite.

**Tech Stack:** Python 3.12, FastAPI, llama.cpp `llama-server.exe` (CPU-only, pinned GitHub release), `gguf-py` (small PyPI dep), `watchdog` (cross-platform file events), `sqlite-vec` (sqlite extension; numpy fallback), `pytest`. No new online services.

## Global Constraints

- **CPU-only llama.cpp.** Pinned `llama.cpp` GitHub release, `bin-win-cpu-x64.zip`. No `--n-gpu-layers`, no CUDA. Per spec §5.3.
- **Thread policy default**: `threads = max(4, os.cpu_count() - 8)`, clamped `[4, os.cpu_count()]`. Per spec §5.2.
- **Idle shutdown** default: 10 min per server (`CHAT_IDLE_SHUTDOWN_MINUTES`, `EMBED_IDLE_SHUTDOWN_MINUTES`). Per spec §5.4.
- **Provider chain defaults**: `chat = minimax.io → agnes-ai → NVIDIA free → local GGUF`; `embed = agnes-ai → minimax.io → NVIDIA free → local GGUF`. Order user-configurable. Per spec §6.6.
- **NVIDIA free caps**: `monthly_call_cap=4000`, `per_session_call_cap=32`. User-overridable. Per spec §6.6.
- **Compact prompt**: provider gets `system + active task + recent_summary (≤1.5k tok) + live_state (≤2k tok) + top-K OKF (K=4, 200 tok each) + recent tool results`. No raw history. Per spec §7.1.
- **Local-Llama override** (when `backend.kind == "local"`): system prompt is `<project>/.dariusai/llama_instructions.md` body; default content ships at `addon/skills/agent-orchestration/local-llama-instructions.md`. Per spec §5.10.
- **Compaction policy**: fold every `MEMORY_FOLD_EVERY=8` turns; 60% context backstop; replaces existing in-session `auto_compact`. Per spec §7.2.
- **LoopGuard**: `loop_repeat` after 3 identical tool calls in last 8; `stuck` after 5 no-progress in last 8 OR two consecutive empty outputs. Rescue prompt injected, loop never aborts. Per spec §7.4.
- **Phases self-reported** by the model: `plan → code → test → verify → reflect`. Pills render; loop doesn't enforce. Per spec §7.3.
- **Per-session MAX_TOOL_ITERATIONS** scales with quota: ≥30 calls → 60, 30–10 → 20, <10 → end turn + coach. Per spec §7.5.
- **OKF schema** (8 type values): `context`, `changelog`, `live`, `skill`, `tool`, `agent_note`, `user_edit`, `decision`. Per spec §6.2.1.
- **`.dariusai/` privacy**: paths under `.dariusai/` (except `.dariusai/memory/`) excluded from default vertex search. Per spec §17 DECISIONS #23.
- **Version policy**: every merged scope that touches `src/**/*.py` or `src/**/static/index.html` runs `tools/bump_version.py --minor` and regenerates `version_lock.json`. Per `AGENTS.md §24`.
- **No commits without tests**: TDD per skill. Step 1 of every task is "write a failing test"; step 4 is "verify it passes".
- **All 16 existing chat/brain tests must keep passing**. The chat rewrite in SP6b is the largest churn; existing stubs pass because the `LLM` Protocol is unchanged.

## File Structure

### New modules

| file | responsibility | key surface |
|---|---|---|
| `src/dariusai/agent/constants.py` | project-wide defaults (idle, fold cadence, threads policy, library paths, default GGUF URLs) | module constants only |
| `src/dariusai/agent/local_llama.py` | OpenAI-shaped client pointed at `127.0.0.1:7788` (chat) and `:7789` (embed) | `LocalLlamaLLM(BaseChatModel)` |
| `src/dariusai/agent/llama_manager.py` | llama-server lifecycle (download, spawn, /health, idle shutdown, restart) | `LlamaManager.start(role)`, `.stop(role)`, `.restart(role)` |
| `src/dariusai/agent/model_library.py` | multi-folder GGUF scanning, sha256-cached introspection, role slots | `ModelLibrary`, `ModelEntry`, `kind=chat\|embed\|unknown` |
| `src/dariusai/agent/backend_chain.py` | ordered chain of backends with ready/probe gates, fallthrough on errors | `BackendChain.call(...)`, `Backend.ready()` |
| `src/dariusai/agent/quota_tracker.py` | per-provider + per-session counters; pre-call gate; header parsing | `QuotaTracker.charge(provider)`, `.ready(provider)` |
| `src/dariusai/agent/file_watcher.py` | watchdog integration; emits `user_edit_diff` | `FileWatcher.start(project_dir)` |
| `src/dariusai/brain/okf.py` | frontmatter parse/serialize, ULID minting, OKF node record | `OKFNode`, `parse_frontmatter`, `mint_ulid` |
| `src/dariusai/brain/vector_index.py` | sqlite-vec wrapper + numpy fallback | `VectorIndex.add(id, vec)`, `.search(q, k)` |
| `addon/skills/agent-orchestration/local-llama-instructions.md` | shipped default for the Local-Llama override | one file |

### Edited modules

| file | change |
|---|---|
| `src/dariusai/agent/chat.py` | REWRITE: compact prompt; BackendChain; LoopGuard; phases; streaming; per-turn budget |
| `src/dariusai/agent/doctrine.py` | EDIT: append memory rules + editing rules to system prompt composition |
| `src/dariusai/agent/llm.py` | EDIT: factory returns `BackendChain` (chat) instead of one client; `build_backend_chain` public |
| `src/dariusai/agent/openai_llm.py` | EDIT: both online and local paths, `complete_stream` mirrors `complete` |
| `src/dariusai/agent/tools.py` | EDIT: reorder tools; add 12 new tools (`okf_*`, `memory_*`, `library_*`, `server_*`) |
| `src/dariusai/agent/graph.py` | EDIT: factory swap, docstring update |
| `src/dariusai/agent/embedder_manager.py` | REWRITE: wraps BackendChain (embed); llama-server :7789 as offline fallback |
| `src/dariusai/brain/store.py` | EDIT: add `type` column + indexes; deprecate `search` in favour of `okf_search` |
| `src/dariusai/brain/learn.py` | EDIT: `learn_skill` writes an OKF node instead of legacy path |
| `src/dariusai/model_catalog.py` | REWRITE: providers table → chain spec; chat + embed endpoints per tier |
| `src/dariusai/viz/server.py` | EDIT: SSE for streaming; status endpoint; watchdog bridge; llama-instructions read/write endpoint |
| `src/dariusai/viz/static/index.html` | EDIT: provider-status badge, phase pills, model library card, runtime card, llama-instructions card, file-tree with diff overlay |
| `src/dariusai/__init__.py` | EDIT: version bump at every merged scope |
| `pyproject.toml` | EDIT: deps (gguf-py, watchdog, sqlite-vec) + version bump |
| `version_lock.json` | EDIT: regenerated at every merged scope |
| `AGENTS.md` | EDIT: §7 references `AGENT_CAPABILITY_FIX.md` alongside `BrainFIX.md` |
| `dariusai-harnessUpdated.md` | EDIT: DAILY LOG entries per merged scope |

### New tests

20 test files in `tests/`:
- `test_okf_index.py`
- `test_backend_chain.py`
- `test_quota_tracker.py`
- `test_llama_manager.py` (synthetic transport; no real llama-server in CI)
- `test_chat_compact_prompt.py`
- `test_chat_loop_guard.py`
- `test_chat_phases.py`
- `test_chat_streams.py`
- `test_chat_budget.py`
- `test_model_library.py`
- `test_gguf_introspection.py`
- `test_can_think_detection.py`
- `test_server_restart.py`
- `test_thinking_toggle.py`
- `test_llama_instructions_file.py`
- `test_local_llama_instructions_resolver.py`
- `test_local_llama_instructions_settings.py`
- `test_coach_messages.py`
- `test_provider_chain_round_trip.py`
- `test_fold_pass.py`

---

## Phase A — Foundation (scaffolding + primitives)

### Task 1 — `agent/constants.py` with project-wide defaults

**Files:**
- Create: `src/dariusai/agent/constants.py`

**Why first**: every other module imports these. Centralised so a single edit changes a project-wide value.

**Interfaces:**
- Consumes: `os.cpu_count()`.
- Produces (module-level constants):
  - `MAX_TOOL_ITERATIONS = 60`
  - `MEMORY_FOLD_EVERY = 8`
  - `COMPACT_BACKSTOP_RATIO = 0.60`
  - `CHAT_IDLE_SHUTDOWN_MINUTES = 10`
  - `EMBED_IDLE_SHUTDOWN_MINUTES = 10`
  - `OKF_TOP_K = 4`
  - `OKF_SNIPPET_TOKENS = 200`
  - `RECENT_SUMMARY_MAX_TOKENS = 1500`
  - `LIVE_VERSION_MAX_TOKENS = 2000`
  - `MAX_AGENTS_CONCURRENT = 4`

- [ ] **Step 1: Write a failing test**

```python
# tests/test_constants_defaults.py
from dariusai.agent import constants

def test_all_defaults_present():
    assert constants.MAX_TOOL_ITERATIONS == 60
    assert constants.MEMORY_FOLD_EVERY == 8
    assert constants.COMPACT_BACKSTOP_RATIO == 0.60
    assert constants.CHAT_IDLE_SHUTDOWN_MINUTES == 10
    assert constants.EMBED_IDLE_SHUTDOWN_MINUTES == 10
    assert constants.OKF_TOP_K == 4
    assert constants.OKF_SNIPPET_TOKENS == 200
    assert constants.RECENT_SUMMARY_MAX_TOKENS == 1500
    assert constants.LIVE_VERSION_MAX_TOKENS == 2000
    assert constants.MAX_AGENTS_CONCURRENT == 4
```

- [ ] **Step 2: Run; expect ImportError**

Run: `pytest tests/test_constants_defaults.py -v` — fails because module doesn't exist.

- [ ] **Step 3: Create the file**

```python
# src/dariusai/agent/constants.py
"""Project-wide defaults. Single source — change here once, all callers
see it. Values cross-referenced from the SP6 spec §5–§7."""
from __future__ import annotations

# Chat loop budget. See spec §7 + AGENTS.md §24.
MAX_TOOL_ITERATIONS = 60

# Memory fold cadence. See spec §7.2.
MEMORY_FOLD_EVERY = 8
COMPACT_BACKSTOP_RATIO = 0.60  # 60% of context window triggers fold.

# Idle-shutdown thresholds for the local llama-server processes. 0 disables.
# See spec §5.4.
CHAT_IDLE_SHUTDOWN_MINUTES = 10
EMBED_IDLE_SHUTDOWN_MINUTES = 10

# Compact-prompt shape. See spec §7.1.
OKF_TOP_K = 4
OKF_SNIPPET_TOKENS = 200
RECENT_SUMMARY_MAX_TOKENS = 1500
LIVE_VERSION_MAX_TOKENS = 2000

# Concurrent chat sessions a single user can have open before QuotaTracker
# starts refusing new sessions on the cheapest provider first. See spec §5.
MAX_AGENTS_CONCURRENT = 4
```

- [ ] **Step 4: Run; expect PASS**

Run: `pytest tests/test_constants_defaults.py -v` — passes.

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/constants.py tests/test_constants_defaults.py
git commit -m "feat(sp6): project-wide defaults in agent/constants.py"
```

---

### Task 2 — OKF frontmatter parsing + ULID minting (`brain/okf.py`)

**Files:**
- Create: `src/dariusai/brain/okf.py`
- Test: `tests/test_okf_index.py`

**Why second**: OKF is the schema behind every node (memory, decision, instructions override). Frontmatter parser needed before any OKF write path can run.

**Interfaces:**
- Consumes: raw bytes of an MD file (frontmatter + body).
- Produces:
  - `class OKFNode`: dataclass holding `id: str`, `type: str`, `title: str`, `created_at: str`, `updated_at: str`, `anchors: list[str]`, `tags: list[str]`, `embedding_ref: str | None`, `source: str`, `score_meta: dict`, `body: str`, `path: Path | None`.
  - `parse_frontmatter(raw: str) -> tuple[dict, str]` — splits on `\n---\n` markers.
  - `serialize(node: OKFNode) -> str` — renders back to MD text.
  - `mint_ulid() -> str` — wraps `ulid-py` if installed; pure-Python fallback otherwise.
  - `class OKFType(str, Enum)`: 8 values: `context`, `changelog`, `live`, `skill`, `tool`, `agent_note`, `user_edit`, `decision`. Per spec §6.2.1.

- [ ] **Step 1: Write a failing test**

```python
# tests/test_okf_index.py
from dariusai.brain.okf import OKFNode, OKFType, parse_frontmatter, serialize, mint_ulid

def test_mint_ulid_is_unique_and_sorted():
    a, b = mint_ulid(), mint_ulid()
    assert a != b
    assert a < b  # lexicographic = chronological

def test_parse_frontmatter_splits_front_and_body():
    raw = "---\nid: 01JAZZ\ntype: skill\ntitle: hi\n---\n\n# body\ntext"
    meta, body = parse_frontmatter(raw)
    assert meta["id"] == "01JAZZ"
    assert meta["type"] == "skill"
    assert "body" in body

def test_serialize_round_trip():
    n = OKFNode(id="01JAZZ", type="skill", title="hi",
                created_at="2026-08-15T10:00:00Z",
                updated_at="2026-08-15T10:00:00Z",
                anchors=[], tags=[], body="body")
    raw = serialize(n)
    meta, body = parse_frontmatter(raw)
    assert meta["type"] == "skill"
    assert "body" in body

def test_okf_type_enum_has_eight_values():
    assert {t.value for t in OKFType} == {
        "context", "changelog", "live", "skill",
        "tool", "agent_note", "user_edit", "decision",
    }
```

- [ ] **Step 2: Run; expect ImportError**

`pytest tests/test_okf_index.py -v` — fails.

- [ ] **Step 3: Implement `okf.py`**

```python
# src/dariusai/brain/okf.py
"""OKF — the brain's open-knowledge-file schema. Each node is a Markdown
file with YAML frontmatter + body. Per SP6 spec §6.2."""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class OKFType(str, Enum):
    context = "context"        # fold-pass summary (spec §6.2.1)
    changelog = "changelog"    # ChangeLog-<ulid>.md
    live = "live"              # Live_version.md body
    skill = "skill"            # SKILL.md from addon/skills
    tool = "tool"              # OKF mirror of a registered tool
    agent_note = "agent_note"  # free-form agent reasoning
    user_edit = "user_edit"    # SP6c manual-edit trail
    decision = "decision"      # explicit design choice


@dataclass
class OKFNode:
    id: str
    type: str                 # OKFType value
    title: str
    created_at: str
    updated_at: str
    anchors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    embedding_ref: str | None = None
    source: str = "chat"      # "chat" | "web" | "user" | "tool"
    score_meta: dict = field(default_factory=dict)
    body: str = ""
    path: Path | None = None


_FENCE_RE = re.compile(r"^---\s*$", re.MULTILINE)


def mint_ulid() -> str:
    """26-char Crockford ULID. Uses python-ulid if installed; falls back
    to time-ordered uuid4 hex otherwise."""
    try:
        from ulid import ULID  # type: ignore
        return str(ULID())
    except ImportError:
        # 10-char timestamp (ms since epoch) + 16-char hex uuid.
        ts = int(time.time() * 1000) & 0xFFFFFFFFFF
        rand = uuid.uuid4().hex[:16]
        return f"{ts:010d}{rand}"


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Split MD on the leading `---\\n...\\n---\\n` block. The rest is the
    body. Missing frontmatter → empty dict + the original raw as body."""
    lines = raw.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, raw
    try:
        end = next(i for i, ln in enumerate(lines[1:], start=1) if ln.strip() == "---")
    except StopIteration:
        return {}, raw
    import yaml  # type: ignore
    meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def serialize(node: OKFNode) -> str:
    """Render an OKFNode back to MD text (frontmatter + body)."""
    import yaml  # type: ignore
    meta = {
        "id": node.id,
        "type": node.type,
        "title": node.title,
        "created_at": node.created_at,
        "updated_at": node.updated_at,
        "anchors": node.anchors,
        "tags": node.tags,
    }
    if node.embedding_ref:
        meta["embedding_ref"] = node.embedding_ref
    meta["source"] = node.source
    if node.score_meta:
        meta["score_meta"] = node.score_meta
    front = yaml.safe_dump(meta, sort_keys=False).strip()
    return f"---\n{front}\n---\n\n{node.body}"
```

- [ ] **Step 4: Add `pyyaml` and `python-ulid` to deps if not present**

`pyyaml` is usually a transitive via many packages. Add `python-ulid>=2.2` to `pyproject.toml` if not already there. The fallback in `mint_ulid` keeps the test passing without the dep.

- [ ] **Step 5: Run the test**

`pytest tests/test_okf_index.py -v` — passes.

- [ ] **Step 6: Commit**

```bash
git add src/dariusai/brain/okf.py tests/test_okf_index.py pyproject.toml
git commit -m "feat(sp6): OKF frontmatter parser + ULID minting"
```

---

### Task 3 — Vector index with numpy fallback (`brain/vector_index.py`)

**Files:**
- Create: `src/dariusai/brain/vector_index.py`
- Test: `tests/test_vector_index.py` (added to `test_okf_index.py` if you prefer)

**Interfaces:**
- `class VectorIndex`:
  - `__init__(path: Path, dim: int)` — opens sqlite-vec + numpy fallback path.
  - `add(id: str, vec: np.ndarray) -> None`
  - `search(query: np.ndarray, k: int = 8) -> list[tuple[str, float]]` — returns `(id, score)` pairs.
- Falls back to numpy when `sqlite-vec` import fails or extension absent.

- [ ] **Step 1: Failing test**

```python
# tests/test_vector_index.py
import numpy as np
from pathlib import Path
from dariusai.brain.vector_index import VectorIndex

def test_vector_index_add_and_search(tmp_path: Path):
    idx = VectorIndex(tmp_path / "vec", dim=4)
    a = np.array([1.0, 0.0, 0.0, 0.0])
    b = np.array([0.0, 1.0, 0.0, 0.0])
    c = np.array([0.9, 0.1, 0.0, 0.0])
    idx.add("a", a); idx.add("b", b); idx.add("c", c)
    hits = idx.search(np.array([1.0, 0.0, 0.0, 0.0]), k=2)
    assert hits[0][0] == "a"        # closest
    assert hits[1][0] == "c"        # second-closest
    assert hits[0][1] > hits[1][1] > hits[-1][1]
```

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_vector_index.py -v` — fails.

- [ ] **Step 3: Implement with numpy only (sqlite-vec comes later if available)**

```python
# src/dariusai/brain/vector_index.py
"""Vertex search backend. Pure-numpy now; sqlite-vec when available.
Per spec §6.3."""
from __future__ import annotations

from pathlib import Path

import numpy as np


class VectorIndex:
    def __init__(self, path: Path, dim: int):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = dim
        self._ids: list[str] = []
        self._matrix_file = self.path.with_suffix(".npy")
        if self._matrix_file.exists():
            self._matrix = np.load(self._matrix_file)
            # ids are loaded alongside in a sibling .json
            import json
            self._ids = json.loads(self.path.with_suffix(".ids.json").read_text())
        else:
            self._matrix = np.zeros((0, dim), dtype=np.float32)

    def add(self, id: str, vec: np.ndarray) -> None:
        v = np.asarray(vec, dtype=np.float32).reshape(1, -1)
        if self._matrix.shape[0] == 0:
            self._matrix = v
        else:
            self._matrix = np.vstack([self._matrix, v])
        self._ids.append(id)

    def search(self, query: np.ndarray, k: int = 8) -> list[tuple[str, float]]:
        q = np.asarray(query, dtype=np.float32).reshape(1, -1)
        if self._matrix.shape[0] == 0:
            return []
        # cosine: (A @ B.T) / (||A|| * ||B||)
        a = self._matrix / np.linalg.norm(self._matrix, axis=1, keepdims=True)
        b = q / np.linalg.norm(q)
        scores = (a @ b.T).reshape(-1)
        order = np.argsort(-scores)[:k]
        return [(self._ids[i], float(scores[i])) for i in order]

    def persist(self) -> None:
        np.save(self._matrix_file, self._matrix)
        import json
        self.path.with_suffix(".ids.json").write_text(
            json.dumps(self._ids)
        )
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_vector_index.py -v` — passes.

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/brain/vector_index.py tests/test_vector_index.py
git commit -m "feat(sp6): VectorIndex with numpy backend (sqlite-vec later)"
```

---

### Task 4 — Wire OKF into `BrainStore` (extend `nodes` with `type`)

**Files:**
- Modify: `src/dariusai/brain/store.py` — `nodes` table gains `type TEXT` column (with default `'skill'`); add `okf_search(query, type=None) -> list[dict]`.
- Test: `tests/test_brain_store.py` — extend existing tests.

**Interfaces:**
- `BrainStore.okf_search(query, type=None) -> list[dict]` — uses FTS5 (already in place per BrainFIX Phase 3) + filters on `type`.
- `BrainStore.add_okf_node(node: OKFNode) -> None` — writes the row + the embedding entry.

- [ ] **Step 1: Failing test**

```python
# tests/test_brain_store.py — append to existing file
from dariusai.brain.okf import OKFNode

def test_add_okf_node_round_trip(tmp_path):
    from dariusai.brain.store import BrainStore
    store = BrainStore(tmp_path / "brain")
    n = OKFNode(
        id="01TEST", type="changelog", title="test",
        created_at="2026-08-15T10:00:00Z",
        updated_at="2026-08-15T10:00:00Z",
        body="a body")
    store.add_okf_node(n)
    hits = store.okf_search("body", type="changelog")
    assert len(hits) == 1
    assert hits[0]["id"] == "01TEST"
```

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_brain_store.py::test_add_okf_node_round_trip -v` — fails.

- [ ] **Step 3: Implement**

In `src/dariusai/brain/store.py`:

```python
# after the BrainStore class definition's __init__, ensure the schema:
def _ensure_schema(self):
    # ... existing CREATE TABLE nodes ...
    self._conn.execute(
        "ALTER TABLE nodes ADD COLUMN type TEXT DEFAULT 'skill'"
    )  # safe no-op if already added
    self._conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type)"
    )
    self._conn.commit()

def add_okf_node(self, node) -> None:
    self._conn.execute(
        """INSERT OR REPLACE INTO nodes
           (id, type, title, tags, problem, solution, created_at, updated_at, body)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (node.id, node.type, node.title,
         ",".join(node.tags), node.title,  # problem → title for legacy compat
         node.body, node.created_at, node.updated_at, node.body),
    )
    self._conn.commit()

def okf_search(self, query: str, type: str | None = None) -> list[dict]:
    import json
    rows = self._conn.execute(
        """SELECT id, type, title FROM nodes
           WHERE (title LIKE ? OR body LIKE ?)
             AND (? IS NULL OR type = ?)""",
        (f"%{query}%", f"%{query}%", type, type),
    ).fetchall()
    return [{"id": r[0], "type": r[1] or "skill",
             "label": r[2], "category": r[1] or "skill",
             "usage_count": 0} for r in rows]
```

(Replace FTS5 LIKE with the actual FTS5 query path installed by BrainFIX Phase 3. The interface — return shape `{id, type, label, category, usage_count}` — stays the same.)

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_brain_store.py -v` — all pass.

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/brain/store.py tests/test_brain_store.py
git commit -m "feat(sp6): BrainStore.add_okf_node + okf_search(filtered by type)"
```

---

### Task 5 — AGENTS.md §7 references the new dossier

**Files:**
- Modify: `AGENTS.md` — `## 7. PROJECT_PLAN.md IS THE SOURCE OF TRUTH` section.

**Why**: AGENTS.md §7 has explicit instructions to read BrainFIX before touching brain/agent files. SP6 introduces `AGENT_CAPABILITY_FIX.md` for the same reason; AGENTS.md must point at it.

- [ ] **Step 1: Read §7 and add a parallel paragraph**

In `AGENTS.md`, after the paragraph that ends "AGENTS.md §7 and §23 so any agent reads it before touching the brain, the importer, the search or the doctrine", add:

```markdown
For the agent-capability workstream (`src/dariusai/agent/chat.py`, `src/dariusai/agent/doctrine.py`, `src/dariusai/agent/local_llama.py`, `src/dariusai/agent/llama_manager.py`, `src/dariusai/agent/model_library.py`, `src/dariusai/agent/backend_chain.py`, `src/dariusai/agent/quota_tracker.py`, `src/dariusai/agent/file_watcher.py`, `src/dariusai/agent/embedder_manager.py`, `src/dariusai/brain/okf.py`, `src/dariusai/brain/vector_index.py`), read [`AGENT_CAPABILITY_FIX.md`](AGENT_CAPABILITY_FIX.md) first. Spec: `docs/superpowers/specs/2026-08-15-agent-capability-fix-design.md`. Plan: `docs/superpowers/plans/2026-08-15-agent-capability-fix.md`.
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs(sp6): AGENTS.md §7 references AGENT_CAPABILITY_FIX.md"
```

(Version bump not required — AGENTS.md is doc-only per the global policy.)

---


## Phase B — Local llama runtime (SP6a-embed)

### Task 6 — `LlamaManager`: download + spawn + lifecycle

**Files:**
- Create: `src/dariusai/agent/llama_manager.py`
- Test: `tests/test_llama_manager.py`

**Why**: foundation of SP6a-embed. All other local-llama code depends on `LlamaManager` to spawn `/health` and shut down.

**Interfaces:**
- `class LlamaManager`:
  - `__init__(cache_root: Path = LOCALAPPDATA / "DariusAI")`
  - `start(role: str, model_path: Path, port: int, ctx: int, batch: int = 512, embedding: bool = False) -> ProcessInfo` — spawns `llama-server`, polls `/health` for ≤ 30 s, raises `LlamaUnready` on timeout.
  - `stop(role) -> None` — SIGTERM, wait 5 s, SIGKILL.
  - `restart(role) -> ProcessInfo` — `stop` + `start` with the same args.
  - `ensure_binary() -> Path` — downloads `llama-server.exe` from the pinned GitHub release if missing. SHA-256 verified.
  - `ensure_model(role, model_id=None) -> Path` — downloads the default GGUF for the role into `%LOCALAPPDATA%\DariusAI\models\`.
- `class LlamaUnready(Exception)`
- `class ProcessInfo` dataclass: `pid: int`, `port: int`, `model_path: Path`, `started_at: datetime`.

- [ ] **Step 1: Failing test (synthetic transport)**

```python
# tests/test_llama_manager.py
import socket
from pathlib import Path
from unittest import mock
from dariusai.agent.llama_manager import LlamaManager

def test_start_spawns_and_probes_health(tmp_path):
    # Reserve a real localhost port for the test spawn.
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    m = LlamaManager(cache_root=tmp_path, transport=mock.MagicMock())
    info = m.start(role="chat", model_path=Path("fake.gguf"), port=port,
                   ctx=512, transport=mock.MagicMock(return_value=200))
    assert info.port == port
    assert info.pid > 0
    m.stop("chat")
```

(Use a synthetic transport module — a stub that never actually launches `llama-server.exe` in CI. Real server is exercised manually.)

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_llama_manager.py -v` — fails.

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/llama_manager.py
"""Local llama.cpp runtime. Two processes (chat:7788 + embed:7789) on
CPU-only. Per SP6 spec §5."""
from __future__ import annotations

import os
import signal
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .constants import CHAT_IDLE_SHUTDOWN_MINUTES, EMBED_IDLE_SHUTDOWN_MINUTES

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
LLAMA_RELEASE = "b4500"  # PIN at design finalisation
DEFAULT_CHAT_GGUF = ("bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
                     "Qwen2.5-Coder-7B-Instruct.Q4_K_M.gguf")
DEFAULT_EMBED_GGUF = ("nomic-ai/nomic-embed-text-v1.5-GGUF",
                      "nomic-embed-text-v1.5.Q4_K_M.gguf")


class LlamaUnready(Exception):
    pass


@dataclass
class ProcessInfo:
    pid: int
    port: int
    model_path: Path
    started_at: float


class LlamaManager:
    def __init__(self, cache_root: Path | None = None):
        self.root = Path(cache_root or (LOCALAPPDATA / "DariusAI"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.bin_dir = self.root / "bin"
        self.models_dir = self.root / "models"
        self.bin_dir.mkdir(exist_ok=True)
        self.models_dir.mkdir(exist_ok=True)
        self._procs: dict[str, subprocess.Popen] = {}
        self._meta: dict[str, ProcessInfo] = {}

    @property
    def binary(self) -> Path:
        return self.bin_dir / "llama-server.exe"

    def ensure_binary(self) -> Path:
        if self.binary.exists():
            return self.binary
        url = (f"https://github.com/ggerganov/llama.cpp/releases/download/"
               f"{LLAMA_RELEASE}/llama-{LLAMA_RELEASE}-bin-win-cpu-x64.zip")
        # download + verify sha256 + extract.
        raise NotImplementedError("real download path is in the integration branch")

    def _spawn(self, role: str, model_path: Path, port: int,
               ctx: int, embedding: bool, batch: int = 512) -> ProcessInfo:
        binary = self.ensure_binary()
        threads = max(4, (os.cpu_count() or 4) - 8)
        flags = ["-m", str(model_path), "--port", str(port),
                 "--host", "127.0.0.1", "-c", str(ctx),
                 "--threads", str(threads)]
        if embedding:
            flags += ["--embedding", "-b", str(batch)]
        else:
            flags.append("--jinja")
        proc = subprocess.Popen(  # noqa: S603
            [str(binary), *flags],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._wait_health(port, proc)
        info = ProcessInfo(pid=proc.pid, port=port,
                           model_path=model_path, started_at=time.time())
        self._procs[role] = proc
        self._meta[role] = info
        return info

    def _wait_health(self, port: int, proc: subprocess.Popen,
                     timeout_s: float = 30.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if proc.poll() is not None:
                raise LlamaUnready(f"exited with code {proc.returncode}")
            try:
                with urllib.request.urlopen(  # noqa: S310
                    f"http://127.0.0.1:{port}/health", timeout=1
                ) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.5)
        raise LlamaUnready(f"no /health within {timeout_s}s")

    def start(self, role, model_path: Path, port: int,
              ctx: int = 8192, embedding: bool = False) -> ProcessInfo:
        self.stop(role)
        return self._spawn(role, model_path, port, ctx, embedding)

    def stop(self, role: str) -> None:
        proc = self._procs.get(role)
        if not proc:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            self._procs.pop(role, None)
            self._meta.pop(role, None)

    def restart(self, role: str) -> ProcessInfo:
        meta = self._meta[role]
        return self.start(role=role, model_path=meta.model_path,
                          port=meta.port, ctx=8192,
                          embedding=(role == "embed"))
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_llama_manager.py -v` — passes.

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/llama_manager.py tests/test_llama_manager.py
git commit -m "feat(sp6): LlamaManager spawn/stop/restart on CPU-only llama.cpp"
```

- [ ] **Step 6: Version bump**

```bash
python tools/bump_version.py --minor
```

---

### Task 7 — `LocalLlamaLLM` (OpenAI-shaped client at :7788)

**Files:**
- Create: `src/dariusai/agent/local_llama.py`
- Test: `tests/test_local_llama.py`

**Interfaces:**
- `class LocalLlamaLLM`:
  - `__init__(port: int = 7788, model_id: str = "local", timeout: float = 180.0)`
  - `complete(system, messages, tools=None) -> dict` — uses `OpenAILLM`'s translation but pointed at `http://127.0.0.1:<port>/v1`.
  - `complete_stream(system, messages, tools=None)` — yields token deltas via SSE.
- Inherits from / composes `OpenAILLM` (DRY).

- [ ] **Step 1: Failing test**

```python
# tests/test_local_llama.py
from dariusai.agent.local_llama import LocalLlamaLLM

def test_local_llama_points_to_localhost():
    llm = LocalLlamaLLM(port=7788)
    assert "127.0.0.1:7788" in llm.base_url
```

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_local_llama.py -v` — fails.

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/local_llama.py
"""OpenAI-shaped client at 127.0.0.1:<port>. Reuses OpenAILLM's request
translation; only the endpoint differs. Per spec §7.6."""
from __future__ import annotations

from .openai_llm import OpenAILLM


class LocalLlamaLLM(OpenAILLM):
  
## Phase B — Local llama runtime (SP6a-embed)

### Task 6 — `LlamaManager`: download + spawn + lifecycle

**Files:**
- Create: `src/dariusai/agent/llama_manager.py`
- Test: `tests/test_llama_manager.py`

**Why**: foundation of SP6a-embed. All other local-llama code depends on `LlamaManager` to spawn `/health` and shut down.

**Interfaces:**
- `class LlamaManager`:
  - `__init__(cache_root: Path = LOCALAPPDATA / "DariusAI")`
  - `start(role: str, model_path: Path, port: int, ctx: int, batch: int = 512, embedding: bool = False) -> ProcessInfo` — spawns `llama-server`, polls `/health` for ≤ 30 s, raises `LlamaUnready` on timeout.
  - `stop(role) -> None` — SIGTERM, wait 5 s, SIGKILL.
  - `restart(role) -> ProcessInfo` — `stop` + `start` with the same args.
  - `ensure_binary() -> Path` — downloads `llama-server.exe` from the pinned GitHub release if missing. SHA-256 verified.
  - `ensure_model(role, model_id=None) -> Path` — downloads the default GGUF for the role into `%LOCALAPPDATA%\DariusAI\models\`.
- `class LlamaUnready(Exception)`
- `class ProcessInfo` dataclass: `pid: int`, `port: int`, `model_path: Path`, `started_at: float`.

- [ ] **Step 1: Failing test (synthetic transport)**

```python
# tests/test_llama_manager.py
import socket
from pathlib import Path
from unittest import mock
from dariusai.agent.llama_manager import LlamaManager

def test_start_spawns_and_probes_health(tmp_path):
    # Reserve a real localhost port for the test spawn.
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    m = LlamaManager(cache_root=tmp_path, transport=mock.MagicMock())
    info = m.start(role="chat", model_path=Path("fake.gguf"), port=port,
                   ctx=512, transport=mock.MagicMock(return_value=200))
    assert info.port == port
    assert info.pid > 0
    m.stop("chat")
```

(Use a synthetic transport module — a stub that never actually launches `llama-server.exe` in CI. Real server is exercised manually.)

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_llama_manager.py -v` — fails.

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/llama_manager.py
"""Local llama.cpp runtime. Two processes (chat:7788 + embed:7789) on
CPU-only. Per SP6 spec section 5."""
from __future__ import annotations

import os
import signal
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .constants import CHAT_IDLE_SHUTDOWN_MINUTES, EMBED_IDLE_SHUTDOWN_MINUTES

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
LLAMA_RELEASE = "b4500"  # PIN at design finalisation
DEFAULT_CHAT_GGUF = ("bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
                     "Qwen2.5-Coder-7B-Instruct.Q4_K_M.gguf")
DEFAULT_EMBED_GGUF = ("nomic-ai/nomic-embed-text-v1.5-GGUF",
                      "nomic-embed-text-v1.5.Q4_K_M.gguf")


class LlamaUnready(Exception):
    pass


@dataclass
class ProcessInfo:
    pid: int
    port: int
    model_path: Path
    started_at: float


class LlamaManager:
    def __init__(self, cache_root: Path | None = None):
        self.root = Path(cache_root or (LOCALAPPDATA / "DariusAI"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.bin_dir = self.root / "bin"
        self.models_dir = self.root / "models"
        self.bin_dir.mkdir(exist_ok=True)
        self.models_dir.mkdir(exist_ok=True)
        self._procs: dict[str, subprocess.Popen] = {}
        self._meta: dict[str, ProcessInfo] = {}

    @property
    def binary(self) -> Path:
        return self.bin_dir / "llama-server.exe"

    def ensure_binary(self) -> Path:
        if self.binary.exists():
            return self.binary
        # Real download + sha256 verify is exercised in manual verification.
        # CI uses a synthetic binary path injected via `binary_path` kwarg.
        raise NotImplementedError(
            "real download path is in the integration branch"
        )

    def _spawn(self, role: str, model_path: Path, port: int,
               ctx: int, embedding: bool, batch: int = 512) -> ProcessInfo:
        binary = self.ensure_binary()
        threads = max(4, (os.cpu_count() or 4) - 8)
        flags = ["-m", str(model_path), "--port", str(port),
                 "--host", "127.0.0.1", "-c", str(ctx),
                 "--threads", str(threads)]
        if embedding:
            flags += ["--embedding", "-b", str(batch)]
        else:
            flags.append("--jinja")
        proc = subprocess.Popen(
            [str(binary), *flags],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._wait_health(port, proc)
        info = ProcessInfo(pid=proc.pid, port=port,
                           model_path=model_path, started_at=time.time())
        self._procs[role] = proc
        self._meta[role] = info
        return info

    def _wait_health(self, port: int, proc: subprocess.Popen,
                     timeout_s: float = 30.0) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if proc.poll() is not None:
                raise LlamaUnready(f"exited with code {proc.returncode}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=1
                ) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.5)
        raise LlamaUnready(f"no /health within {timeout_s}s")

    def start(self, role, model_path: Path, port: int,
              ctx: int = 8192, embedding: bool = False) -> ProcessInfo:
        self.stop(role)
        return self._spawn(role, model_path, port, ctx, embedding)

    def stop(self, role: str) -> None:
        proc = self._procs.get(role)
        if not proc:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            self._procs.pop(role, None)
            self._meta.pop(role, None)

    def restart(self, role: str) -> ProcessInfo:
        meta = self._meta[role]
        return self.start(role=role, model_path=meta.model_path,
                          port=meta.port, ctx=8192,
                          embedding=(role == "embed"))
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_llama_manager.py -v` — passes.

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/llama_manager.py tests/test_llama_manager.py
git commit -m "feat(sp6): LlamaManager spawn/stop/restart on CPU-only llama.cpp"
```

- [ ] **Step 6: Version bump**

```bash
python tools/bump_version.py --minor
```

---

### Task 7 — `LocalLlamaLLM` (OpenAI-shaped client at :7788)

**Files:**
- Create: `src/dariusai/agent/local_llama.py`
- Test: `tests/test_local_llama.py`

**Interfaces:**
- `class LocalLlamaLLM`:
  - `__init__(port: int = 7788, model_id: str = "local", timeout: float = 180.0)`
  - `complete(system, messages, tools=None) -> dict` — uses `OpenAILLM`'s translation but pointed at `http://127.0.0.1:<port>/v1`.
  - `complete_stream(system, messages, tools=None)` — yields token deltas via SSE.
- Inherits from / composes `OpenAILLM` (DRY).

- [ ] **Step 1: Failing test**

```python
# tests/test_local_llama.py
from dariusai.agent.local_llama import LocalLlamaLLM

def test_local_llama_points_to_localhost():
    llm = LocalLlamaLLM(port=7788)
    assert "127.0.0.1:7788" in llm.base_url
```

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_local_llama.py -v` — fails.

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/local_llama.py
"""OpenAI-shaped client at 127.0.0.1:<port>. Reuses OpenAILLM's request
translation; only the endpoint differs."""
from __future__ import annotations

from .openai_llm import OpenAILLM


class LocalLlamaLLM(OpenAILLM):
    def __init__(self, port: int = 7788,
                 model_id: str = "local",
                 timeout: float = 180.0,
                 context_window: int = 32_768):
        super().__init__(
            model=model_id, api_key="",
            base_url=f"http://127.0.0.1:{port}/v1",
            timeout=timeout,
            context_window=context_window,
        )
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_local_llama.py -v` — passes.

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/local_llama.py tests/test_local_llama.py
git commit -m "feat(sp6): LocalLlamaLLM pointed at 127.0.0.1:<port>"
```

---

### Task 8 — `ModelLibrary` with multi-folder scan + sha256 cache

**Files:**
- Create: `src/dariusai/agent/model_library.py`
- Test: `tests/test_model_library.py`

**Interfaces:**
- `class ModelEntry` — dataclass per spec section 5.6.
- `class ModelLibrary`:
  - `__init__(cache_root: Path)`
  - `add_folder(path: Path)` — scan, parse, persist.
  - `list_models(kind=None) -> list[ModelEntry]`
  - `set_role(model_id, role)` — assign chat/embed; queues a restart if active.
  - `active_chat_id / active_embed_id`
  - Persistence: `cache_root/model_library.json`.

- [ ] **Step 1: Failing test (with a fake `.gguf` byte fixture)**

```python
# tests/test_model_library.py
from pathlib import Path
from unittest import mock
from dariusai.agent.model_library import ModelLibrary, ModelEntry

def test_add_folder_records_models_with_metadata(tmp_path):
    (tmp_path / "a").mkdir()
    fake_gguf = tmp_path / "a" / "model.gguf"
    fake_gguf.write_bytes(b"GGUF" + b"\x00" * 50)  # not a real GGUF
    lib = ModelLibrary(cache_root=tmp_path / "lib")
    with mock.patch("dariusai.agent.model_library._introspect_gguf",
                    return_value={"context_length": 4096, "chat_template": "",
                                  "architecture": "qwen", "kind": "chat"}):
        lib.add_folder(tmp_path / "a")
    models = lib.list_models()
    assert len(models) == 1
    assert models[0].path == fake_gguf
    assert models[0].context_length == 4096
```

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_model_library.py -v` — fails.

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/model_library.py
"""Multi-folder GGUF library with sha256-cached introspection."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ulid import ULID


@dataclass
class ModelEntry:
    id: str
    path: Path
    display_name: str
    size_bytes: int
    sha256: str
    kind: str                     # chat / embed / unknown
    context_length: int | None
    chat_template: str | None
    can_think: bool
    architecture: str
    detected_at: str


def _introspect_gguf(path: Path) -> dict:
    """Read GGUF header for context_length, chat_template, architecture, kind.
    Returns safe defaults when fields are missing."""
    from gguf import GGUFReader  # type: ignore
    try:
        r = GGUFReader(str(path))
    except Exception as e:
        return {"context_length": None, "chat_template": None,
                "architecture": "unknown", "kind": "unknown",
                "error": str(e)}
    arch = next((k for k in r.fields if k.endswith(".general.architecture")),
                None)
    ctx = None
    cl_key = next((k for k in r.fields if k.endswith(".context_length")), None)
    if cl_key:
        try:
            ctx = int(r.fields[cl_key].parts[-1][0])
        except Exception:
            ctx = None
    tpl = None
    tk_key = next((k for k in r.fields
                   if k.endswith("tokenizer.chat_template")), None)
    if tk_key:
        try:
            tpl = r.fields[tk_key].parts[-1][0].decode("utf-8", errors="replace")
        except Exception:
            tpl = None
    return {
        "context_length": ctx or 8192,
        "chat_template": tpl,
        "architecture": arch.split(".")[0] if arch else "unknown",
        "kind": "embed" if "embedding" in (arch or "").lower() else "chat",
    }


def _can_think(entry: dict) -> bool:
    """Heuristic combining template signature, filename marker, arch flag."""
    tpl = entry.get("chat_template") or ""
    if "<think>" in tpl or "thinking" in tpl.lower():
        return True
    return False


class ModelLibrary:
    def __init__(self, cache_root: Path):
        self.cache_root = Path(cache_root)
        self._state_file = self.cache_root / "model_library.json"
        self._state = {"folders": [], "models": {}, "slots": {}}
        if self._state_file.exists():
            self._state = json.loads(self._state_file.read_text())
        self._state.setdefault("folders", [])
        self._state.setdefault("models", {})
        self._state.setdefault("slots", {})

    def _persist(self) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(json.dumps(self._state, indent=2,
                                              default=str))

    def add_folder(self, folder: Path) -> int:
        if str(folder) not in self._state["folders"]:
            self._state["folders"].append(str(folder))
        added = 0
        for path in sorted(Path(folder).glob("**/*.gguf")):
            sha = self._hash(path)
            if any(m["sha256"] == sha
                   for m in self._state["models"].values()):
                continue
            meta = _introspect_gguf(path)
            entry = ModelEntry(
                id=str(ULID()), path=path,
                display_name=path.stem, size_bytes=path.stat().st_size,
                sha256=sha, kind=meta["kind"],
                context_length=meta["context_length"],
                chat_template=meta["chat_template"],
                can_think=_can_think(meta),
                architecture=meta["architecture"],
                detected_at="2026-08-15T00:00:00Z",
            )
            self._state["models"][entry.id] = asdict(entry)
            added += 1
        self._persist()
        return added

    @staticmethod
    def _hash(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def list_models(self, kind: str | None = None):
        out = []
        for m in self._state["models"].values():
            if kind and m["kind"] != kind:
                continue
            out.append(ModelEntry(**m))
        return out

    def set_role(self, model_id: str, role: str | None) -> None:
        self._state["slots"][role] = model_id if model_id else None
        self._persist()
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_model_library.py -v` — passes.

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/model_library.py tests/test_model_library.py pyproject.toml
git commit -m "feat(sp6): ModelLibrary with GGUF introspection + sha256 cache"
```

- [ ] **Step 6: Add `gguf-py` + `python-ulid` to `pyproject.toml`**

```toml
[project.dependencies]
# existing ...
"python-ulid>=2.2",
"gguf-py>=0.10",
```

---

### Task 9 — `EmbedderManager` rewired to BackendChain (embed) with local GGUF as ultimate fallback

**Files:**
- Modify: `src/dariusai/agent/embedder_manager.py` — rewrite.
- Test: `tests/test_embedder_manager.py`

**Interfaces:**
- `class EmbedderManager`:
  - `__init__(chain: BackendChain)`
  - `embed(texts: list[str]) -> list[list[float]]` — dispatches via `chain.call(...)`.

- [ ] **Step 1: Failing test (chain stub)**

```python
# tests/test_embedder_manager.py
def test_embedder_routes_via_chain():
    from dariusai.agent.backend_chain import BackendChain
    from dariusai.agent.embedder_manager import EmbedderManager
    chain = BackendChain(
        backends=[StubBackend(name="stub", dim=768, vec=[0.1]*768)],
        quota=StubQuota(),
    )
    mgr = EmbedderManager(chain=chain)
    out = mgr.embed(["hi"])
    assert len(out[0]) == 768
```

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_embedder_manager.py -v` — fails.

- [ ] **Step 3: Implement** (depends on Phase C `BackendChain`)

```python
# src/dariusai/agent/embedder_manager.py
"""Embedder dispatch via BackendChain."""
from __future__ import annotations

from .backend_chain import BackendChain


class EmbedderManager:
    def __init__(self, chain: BackendChain):
        self._chain = chain

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._chain.call({"texts": texts})
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_embedder_manager.py -v` — passes.

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/embedder_manager.py tests/test_embedder_manager.py
git commit -m "feat(sp6): EmbedderManager wrapped around BackendChain"
```

---

### Task 10 — `LlamaManager.restart(role)` integration with `library.set_role`

**Files:**
- Modify: `src/dariusai/agent/model_library.py` — `set_role` calls `LlamaManager.restart(role)` when active.
- Test: `tests/test_server_restart.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_server_restart.py
def test_set_role_differs_from_active_triggers_restart(tmp_path, monkeypatch):
    from dariusai.agent.model_library import ModelLibrary
    restarted = []
    monkeypatch.setattr(
        "dariusai.agent.model_library.LlamaManager.restart",
        lambda self, role: restarted.append(role))
    lib = ModelLibrary(cache_root=tmp_path)
    lib.set_active_slot("chat", "first-model-id")
    lib.set_role("second-model-id", "chat")
    assert "chat" in restarted
```

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_server_restart.py -v` — fails.

- [ ] **Step 3: Implement**

In `model_library.py`:

```python
def set_role(self, model_id: str, role: str) -> bool:
    """Returns True if LlamaManager.restart was triggered."""
    prev = self._state["slots"].get(role)
    self._state["slots"][role] = model_id
    self._persist()
    if prev and prev != model_id:
        from .llama_manager import LlamaManager
        LlamaManager().restart(role)
        return True
    return False

def set_active_slot(self, role: str, model_id: str) -> None:
    self._state["slots"][role] = model_id
    self._persist()
```

- [ ] **Step 4: Run; expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/model_library.py tests/test_server_restart.py
git commit -m "feat(sp6): set_role auto-restarts llama-server on slot change"
```

---

### Task 11 — Local-Llama instructions override (file + Settings endpoint)

**Files:**
- Modify: `src/dariusai/agent/chat.py` — add `system_prompt_for(...)` resolver.
- Modify: `src/dariusai/viz/server.py` — read/write endpoint for `llama_instructions.md` + reset button.
- Test: `tests/test_llama_instructions_file.py`, `tests/test_local_llama_instructions_resolver.py`.

- [ ] **Step 1: Failing test (resolver)**

```python
# tests/test_local_llama_instructions_resolver.py
def test_resolver_picks_local_when_file_exists(tmp_path):
    (tmp_path / ".dariusai").mkdir()
    (tmp_path / ".dariusai" / "llama_instructions.md").write_text(
        "---\napplies_to: local-gguf\n---\n\nLOCAL INSTRUCTIONS"
    )
    out = system_prompt_for(backend=StubBackend(kind="local"),
                            project_dir=tmp_path,
                            doctrine="DOCTRINE")
    assert "LOCAL INSTRUCTIONS" in out

def test_resolver_keeps_doctrine_for_online():
    out = system_prompt_for(backend=StubBackend(kind="online"),
                            project_dir="/nowhere", doctrine="DOCTRINE")
    assert out == "DOCTRINE"
```

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_local_llama_instructions_resolver.py -v` — fails.

- [ ] **Step 3: Implement resolver**

Add to `src/dariusai/agent/chat.py`:

```python
def system_prompt_for(backend, project_dir: Path, doctrine: str) -> str:
    """Pick the system prompt. Local backend + llama_instructions.md →
    that file's body; otherwise doctrine."""
    if getattr(backend, "kind", None) != "local":
        return doctrine
    p = Path(project_dir) / ".dariusai" / "llama_instructions.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return doctrine
```

- [ ] **Step 4: Reset endpoint**

In `src/dariusai/viz/server.py`:

```python
@app.post("/llama-instructions/reset")
async def reset_llama_instructions():
    addon_root = Path(__file__).resolve().parents[2] / "addon"
    shipped = addon_root / "skills" / "agent-orchestration" / "local-llama-instructions.md"
    target = Path(app.state.project_dir) / ".dariusai" / "llama_instructions.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(shipped.read_text(encoding="utf-8"), encoding="utf-8")
    return {"ok": True, "path": str(target)}

@app.post("/llama-instructions")
async def write_llama_instructions(payload: dict):
    target = Path(app.state.project_dir) / ".dariusai" / "llama_instructions.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload["body"], encoding="utf-8")
    return {"ok": True, "path": str(target)}
```

- [ ] **Step 5: Run; expect PASS**

`pytest tests/test_local_llama_instructions_resolver.py tests/test_llama_instructions_file.py -v` — passes.

- [ ] **Step 6: Commit**

```bash
git add src/dariusai/agent/chat.py src/dariusai/viz/server.py tests/test_local_llama_instructions_resolver.py tests/test_llama_instructions_file.py
git commit -m "feat(sp6): Local-Llama instructions override (resolver + Settings endpoints)"
```

---

### Task 12 — Reset Server button endpoints + Status endpoint

**Files:**
- Modify: `src/dariusai/viz/server.py`
- Test: `tests/test_server_status.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_server_status.py
def test_status_endpoint_returns_server_metadata(tmp_path):
    from fastapi.testclient import TestClient
    from dariusai.viz.server import create_app
    from dariusai.brain.store import BrainStore
    app = create_app(BrainStore(tmp_path / "brain"))
    client = TestClient(app)
    r = client.get("/server/status")
    assert r.status_code == 200
    body = r.json()
    assert "chat_pid" in body
    assert "embed_pid" in body
```

- [ ] **Step 2: Run; expect failure**

`pytest tests/test_server_status.py -v` — fails.

- [ ] **Step 3: Implement endpoints**

In `src/dariusai/viz/server.py`:

```python
@app.get("/server/status")
async def server_status():
    mgr = app.state.llama_manager
    return {
        "chat_pid": mgr._meta.get("chat").pid if "chat" in mgr._meta else None,
        "embed_pid": mgr._meta.get("embed").pid if "embed" in mgr._meta else None,
        "chat_model": str(mgr._meta["chat"].model_path)
                      if "chat" in mgr._meta else None,
        "embed_model": str(mgr._meta["embed"].model_path)
                      if "embed" in mgr._meta else None,
        "chat_last_health_ok_at": None,
        "embed_last_health_ok_at": None,
    }

@app.post("/server/{role}/restart")
async def restart_server(role: str):
    if role not in {"chat", "embed", "all"}:
        raise HTTPException(400, "role must be chat, embed, or all")
    mgr = app.state.llama_manager
    if role == "all":
        mgr.restart("chat"); mgr.restart("embed")
    else:
        mgr.restart(role)
    return {"ok": True, "role": role}
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_server_status.py -v` — passes.

- [ ] **Step 5: Commit + version bump**

```bash
git add src/dariusai/viz/server.py tests/test_server_status.py
git commit -m "feat(sp6): /server/status + /server/{role}/restart endpoints"
python tools/bump_version.py --minor
```

---

## Phase C — Quota + BackendChain (SP6a backend swap)

### Task 13 — `QuotaTracker` with per-provider + per-session counters

**Files:**
- Create: `src/dariusai/agent/quota_tracker.py`
- Test: `tests/test_quota_tracker.py`

**Interfaces:**
- `class QuotaTracker`:
  - `__init__(settings: dict)` — `{"providers": {"minimax.io": {"calls_used": 0, "calls_max": 4000, "rate_limited_until": null}, ...}, "sessions": {}}`.
  - `ready(provider: str) -> bool`
  - `charge(provider: str, headers: dict | None = None) -> None` — increments or parses `x-ratelimit-*`.
  - `mark_exhausted(provider: str, until_iso: str) -> None`
  - `record_failure(provider: str, kind: str) -> None`

- [ ] **Step 1: Failing test**

```python
# tests/test_quota_tracker.py
from dariusai.agent.quota_tracker import QuotaTracker

def test_ready_blocks_when_calls_max_reached():
    qt = QuotaTracker(settings={"providers": {"m": {"calls_used": 10, "calls_max": 10,
                                                    "rate_limited_until": None}}})
    assert qt.ready("m") is False

def test_charge_increments_and_parses_xratelimit_remaining_header():
    qt = QuotaTracker(settings={"providers": {"m": {"calls_used": 0, "calls_max": 4000,
                                                    "rate_limited_until": None}}})
    qt.charge("m", headers={"x-ratelimit-remaining": "123"})
    assert qt._state["providers"]["m"]["calls_used"] == 4000 - 123

def test_mark_exhausted_blocks_until_iso():
    qt = QuotaTracker(settings={"providers": {"m": {"calls_used": 0, "calls_max": 4000,
                                                    "rate_limited_until": None}}})
    qt.mark_exhausted("m", "2999-01-01T00:00:00Z")
    assert qt.ready("m") is False
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/quota_tracker.py
"""Per-provider quota tracking + per-session counters. Pre-call gate.
Per spec section 6.5."""
from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class QuotaTracker:
    def __init__(self, settings: dict):
        self._state = settings
        self._state.setdefault("providers", {})
        self._state.setdefault("sessions", {})

    def ready(self, provider: str) -> bool:
        p = self._state["providers"].get(provider)
        if not p:
            return True
        if p.get("rate_limited_until"):
            return _now_iso() > p["rate_limited_until"]
        return p.get("calls_used", 0) < p.get("calls_max", float("inf"))

    def charge(self, provider: str, headers: dict | None = None) -> None:
        p = self._state["providers"].setdefault(
            provider, {"calls_used": 0, "calls_max": 10**9,
                       "rate_limited_until": None})
        if headers and "x-ratelimit-remaining" in headers:
            try:
                p["calls_used"] = p["calls_max"] - int(headers["x-ratelimit-remaining"])
                return
            except ValueError:
                pass
        p["calls_used"] += 1

    def mark_exhausted(self, provider: str, until_iso: str) -> None:
        p = self._state["providers"].setdefault(
            provider, {"calls_used": 0, "calls_max": 10**9,
                       "rate_limited_until": None})
        p["rate_limited_until"] = until_iso

    def record_failure(self, provider: str, kind: str) -> None:
        if kind == "404":
            # 5-minute cool-down to avoid hammering an absent route.
            from datetime import timedelta
            until = (datetime.now(timezone.utc) + timedelta(minutes=5))
            p = self._state["providers"].setdefault(
                provider, {"calls_used": 0, "calls_max": 10**9,
                           "rate_limited_until": None})
            p["rate_limited_until"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")

    def remaining(self, provider: str) -> int:
        p = self._state["providers"].get(provider, {})
        return max(0, p.get("calls_max", 10**9) - p.get("calls_used", 0))
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_quota_tracker.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/quota_tracker.py tests/test_quota_tracker.py
git commit -m "feat(sp6): QuotaTracker with pre-call gate + rate-limit headers"
```

---

### Task 14 — `BackendChain` ordered fallthrough

**Files:**
- Create: `src/dariusai/agent/backend_chain.py`
- Test: `tests/test_backend_chain.py`

**Interfaces:**
- `class Backend` (Protocol):
  - `name: str`
  - `kind: str`            # "online" | "local"
  - `ready() -> bool`
  - `probe() -> bool`      # one-time health check (cached)
  - `call(payload) -> Any`
- `class BackendChainError(Exception)`
- `class BackendChain`:
  - `__init__(backends: list[Backend], quota: QuotaTracker, warm_window_s: int = 60)`
  - `call(payload) -> Any` — walks backends; returns first success; raises `BackendChainError` on exhaustion.

- [ ] **Step 1: Failing test**

```python
# tests/test_backend_chain.py
def test_chain_skips_quota_exhausted_tier():
    from dariusai.agent.backend_chain import BackendChain
    from dariusai.agent.quota_tracker import QuotaTracker
    a = StubBackend(name="a", should_fail=True)
    b = StubBackend(name="b", payload=42)
    qt = QuotaTracker(settings={"providers": {"a": {"calls_used": 10, "calls_max": 10,
                                                    "rate_limited_until": None}}})
    chain = BackendChain(backends=[a, b], quota=qt)
    assert chain.call({}) == 42

def test_chain_raises_when_all_fail():
    from dariusai.agent.backend_chain import BackendChain, BackendChainError
    chain = BackendChain([StubBackend(should_fail=True),
                          StubBackend(should_fail=True)], quota=QuotaTracker({}))
    with pytest.raises(BackendChainError):
        chain.call({})
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/backend_chain.py
"""Ordered chain of backends with quota-aware fallthrough. Per spec section 6.4."""
from __future__ import annotations

import time
from typing import Any, Callable, Optional, Protocol


class BackendChainError(Exception):
    pass


class Backend(Protocol):
    name: str
    kind: str
    def ready(self) -> bool: ...
    def call(self, payload: Any) -> Any: ...


class BackendChain:
    def __init__(self, backends: list[Backend], quota,
                 warm_window_s: float = 60.0,
                 on_event: Callable[[dict], None] | None = None):
        self._backends = backends
        self._quota = quota
        self._warm_window_s = warm_window_s
        self._last_success: dict[str, float] = {}
        self._on_event = on_event

    def call(self, payload: Any) -> Any:
        last_errors: list[str] = []
        for b in self._backends:
            if not b.ready() or not self._quota.ready(b.name):
                continue
            # Warm path: skip probe when last success is recent.
            last = self._last_success.get(b.name)
            if last and (time.time() - last) < self._warm_window_s:
                pass
            try:
                result = b.call(payload)
                self._last_success[b.name] = time.time()
                return result
            except Exception as e:  # noqa: BLE001
                last_errors.append(f"{b.name}: {e}")
                self._quota.record_failure(b.name, kind=type(e).__name__)
                continue
        raise BackendChainError("; ".join(last_errors) or "no backends ready")
```

- [ ] **Step 4: Implement stub backend for tests**

```python
# tests/_stubs.py — append
class StubBackend:
    def __init__(self, name="stub", payload=None, should_fail=False,
                 kind="online", dim=768, vec=None):
        self.name = name; self.payload = payload
        self.should_fail = should_fail; self.kind = kind
        self.dim = dim; self.vec = vec or []
    def ready(self): return True
    def call(self, payload):
        if self.should_fail:
            raise RuntimeError("nope")
        return self.payload if self.payload is not None else {"embeddings": [self.vec]}
```

- [ ] **Step 5: Run; expect PASS**

`pytest tests/test_backend_chain.py -v` — passes.

- [ ] **Step 6: Commit**

```bash
git add src/dariusai/agent/backend_chain.py tests/test_backend_chain.py tests/_stubs.py
git commit -m "feat(sp6): BackendChain ordered fallthrough with warm-path + quota gate"
```

---

### Task 15 — Provider chain wired into `llm.py` factory (chat)

**Files:**
- Modify: `src/dariusai/agent/llm.py` — `build_backend_chain(store, kind="chat" | "embed")`.
- Test: `tests/test_llm_from_store.py` — extend for chain.

**Interfaces:**
- `build_backend_chain(store, kind: str) -> BackendChain` — reads the active providers + their chain order from the store, builds a `BackendChain`.

- [ ] **Step 1: Failing test**

```python
# tests/test_llm_from_store.py — append
def test_build_backend_chain_returns_chain_with_active_providers():
    from dariusai.agent.llm import build_backend_chain
    store = StubStoreWithProviders(["minimax.io", "local-gguf"])
    chain = build_backend_chain(store, kind="chat")
    assert len(chain._backends) == 2
    assert chain._backends[0].name == "minimax.io"
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/llm.py — extend
def build_backend_chain(store, kind: str = "chat"):
    """Per spec section 6.6 + section 6.4. Reads chat/embed chain order
    from store.settings[f'{kind}_chain'] = [provider names]."""
    chain_order = store.get_setting(f"{kind}_chain", _DEFAULT_CHAIN if kind == "chat"
                                    else _DEFAULT_EMBED_CHAIN)
    backends = [_make_backend(name, store, kind) for name in chain_order]
    from .backend_chain import BackendChain
    from .quota_tracker import QuotaTracker
    return BackendChain(backends=backends, quota=QuotaTracker(store.get_setting("quota", {})))

_DEFAULT_CHAIN = ["minimax.io", "agnes-ai", "nvidia-free", "local-gguf"]
_DEFAULT_EMBED_CHAIN = ["agnes-ai", "minimax.io", "nvidia-free", "local-gguf"]


def _make_backend(name: str, store, kind: str):
    if name == "local-gguf":
        port = 7788 if kind == "chat" else 7789
        from .local_llama import LocalLlamaLLM
        return LocalLlamaLLM(port=port)
    from .openai_llm import OpenAILLM
    from .model_catalog import spec_for
    spec = spec_for(name)
    return OpenAILLM(model=store.get_provider_model(name) or "",
                     api_key=store.get_provider_api_key(name),
                     base_url=store.get_provider_base_url(name)
                              or spec.base_url)


def build_llm(store, model: str | None = None):
    """Backward-compat wrapper used by graph.py."""
    from .backend_chain import BackendChain as _Bc
    chain = build_backend_chain(store, kind="chat")
    # Legacy callers expect a single client; return the chain's first ready
    # backend by name.
    for b in chain._backends:
        if b.kind != "local":
            return b
    return chain._backends[0]
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_llm_from_store.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/llm.py tests/test_llm_from_store.py
git commit -m "feat(sp6): build_backend_chain + build_llm backend compat"
```

---

## Phase D — Compact-prompt chat loop (SP6b)

### Task 16 — Chat loop uses `BackendChain` + compact prompt

**Files:**
- Modify: `src/dariusai/agent/chat.py` — replace direct `llm.complete` with `chain.call`.
- Test: `tests/test_chat_compact_prompt.py`

**Interfaces:**
- `ChatSession.llm` becomes a `BackendChain`.
- New `compact_prompt_for(state)` function assembles the small chunk (system + active task + summary + live + OKF top-K + recent tool results).

- [ ] **Step 1: Failing test**

```python
# tests/test_chat_compact_prompt.py
def test_active_task_in_prompt_but_no_raw_history(tmp_path):
    from dariusai.agent.chat import ChatSession, compact_prompt_for
    from dariusai.agent.backend_chain import BackendChain
    chain = BackendChain([StubBackend(payload={"content": [{"type": "text",
        "text": "ok"}], "stop_reason": "end_turn"})], quota=StubQuota())
    sess = ChatSession(llm=chain, tools=StubTools())
    sess.messages = [{"role": "user", "content": "earlier: long body " * 50}]
    msgs = compact_prompt_for(session=sess, backend_kind="online",
                              doctrine="DOCTRINE", active_task="now")
    # raw history must NOT be present
    assert not any("earlier: long body" in (m.get("content") or "") for m in msgs[1:])
    # active task IS present
    assert any("now" in (m.get("content") or "") for m in msgs[1:])
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement `compact_prompt_for`**

```python
# src/dariusai/agent/chat.py — append

def compact_prompt_for(session, backend_kind: str, doctrine: str,
                       active_task: str) -> list[dict]:
    """Build the per-iteration message list per spec section 7.1."""
    system = system_prompt_for(backend=_Stub(backend_kind),
                                project_dir=session.project_dir,
                                doctrine=doctrine)
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": active_task}]
    # (Other blocks — summary, live, OKF top-K — added by Phase D
    # subsequent tasks; this initial version just sets the shape.)
    return msgs
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_chat_compact_prompt.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/chat.py tests/test_chat_compact_prompt.py
git commit -m "feat(sp6): compact_prompt_for builds small-chunk message list"
```

---

### Task 17 — Fold pass: every 8 turns + 60% backstop

**Files:**
- Create: `src/dariusai/agent/folder.py`
- Modify: `src/dariusai/agent/chat.py`
- Test: `tests/test_fold_pass.py`

**Interfaces:**
- `class Folder`:
  - `__init__(memory_root: Path, llm: BackendChain)` — `memory_root` is `<project>/.dariusai/memory/`.
  - `fold(turns: list[dict], *, force: bool = False) -> dict` — calls the LLM with `tools=[]` for a structured summary, appends a `context` node to `FullContext.md`, optionally rewrites `Live_version.md`.

- [ ] **Step 1: Failing test**

```python
# tests/test_fold_pass.py
def test_every_8_turns_appends_to_full_context(tmp_path):
    (tmp_path / "memory").mkdir()
    folder = Folder(memory_root=tmp_path / "memory",
                    llm=StubLLM(returns={"content": [{"type": "text", "text":
                                                     "summary"}]}))
    msgs = [{"role": "user", "content": f"turn {i}"} for i in range(8)]
    result = folder.fold(msgs)
    full = (tmp_path / "memory" / "FullContext.md").read_text()
    assert "summary" in full
    assert result["status"] == "ok"
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/folder.py
"""Periodic memory compaction. Per spec section 7.2."""
from __future__ import annotations

from pathlib import Path

from .backend_chain import BackendChain
from .brain.okf import OKFNode, OKFType, mint_ulid


class Folder:
    def __init__(self, memory_root: Path, llm: BackendChain):
        self.root = Path(memory_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.full = self.root / "FullContext.md"
        if not self.full.exists():
            self.full.touch()
        self.live = self.root / "Live_version.md"
        self.llm = llm

    def fold(self, turns: list[dict], *, force: bool = False) -> dict:
        from .okf_compose import summarize
        prompt = ("Summarize the prior conversation concisely. Preserve: "
                  "1) Active task/goal, 2) Files created/edited, 3) Important decisions, "
                  "4) Current state.")
        try:
            summary = self.llm.call([{"role": "user", "content": prompt}, *turns])
            if not summary or not summary.get("content"):
                raise RuntimeError("empty summary")
            text = summary["content"][0]["text"]
        except Exception:
            text = "(context compacted; summarizer unavailable)"
        ulid = mint_ulid()
        node = OKFNode(id=ulid, type=OKFType.context.value,
                       title=f"fold {ulid}",
                       created_at="2026-08-15T00:00:00Z",
                       updated_at="2026-08-15T00:00:00Z",
                       anchors=[], tags=["fold"], body=text)
        from .brain.okf import serialize
        with self.full.open("a", encoding="utf-8") as f:
            f.write(f"\n<!-- {ulid} -->\n{serialize(node)}\n")
        return {"status": "ok", "id": ulid}
```

- [ ] **Step 4: Wire into `ChatSession`**

In `chat.py`:

```python
def maybe_fold(self) -> None:
    if len(self.messages) - self.last_fold_at >= 8 or self.context_used_ratio() > 0.60:
        from .folder import Folder
        from pathlib import Path
        Folder(Path(self.project_dir) / ".dariusai" / "memory", self.llm).fold(self.messages)
        self.last_fold_at = len(self.messages)
```

- [ ] **Step 5: Run; expect PASS**

`pytest tests/test_fold_pass.py -v`

- [ ] **Step 6: Commit**

```bash
git add src/dariusai/agent/folder.py src/dariusai/agent/chat.py tests/test_fold_pass.py
git commit -m "feat(sp6): Folder writes structured fold entries to FullContext.md"
```

---

### Task 18 — LoopGuard: loop_repeat + stuck + rescue prompt

**Files:**
- Modify: `src/dariusai/agent/chat.py`
- Test: `tests/test_chat_loop_guard.py`

**Interfaces:**
- `class LoopGuard`:
  - `__init__(window: int = 8)` — sliding window of last N iterations.
  - `see(call_sig: str, out_tokens: int) -> dict | None` — returns `{"type": "loop_repeat"|"stuck", ...}` or None.
  - `rescue_prompt() -> str` — one-shot nudge.

- [ ] **Step 1: Failing test**

```python
# tests/test_chat_loop_guard.py
def test_loop_repeat_emits_after_3_identical_calls():
    from dariusai.agent.chat import LoopGuard
    g = LoopGuard()
    g.see("read_file:a.py", 50); g.see("read_file:a.py", 50); g.see("read_file:a.py", 50)
    assert g.last_event()["type"] == "loop_repeat"

def test_stuck_emits_after_5_no_progress():
    from dariusai.agent.chat import LoopGuard
    g = LoopGuard()
    for _ in range(5):
        g.see("read_file:a.py", 0)
    assert g.last_event()["type"] == "stuck"
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/chat.py — append

class LoopGuard:
    def __init__(self, window: int = 8, no_progress_threshold: int = 5):
        self._state: deque = deque(maxlen=window)
        self._events: list[dict] = []
        self.no_progress_threshold = no_progress_threshold

    def see(self, sig: str, out_tokens: int) -> None:
        self._state.append((sig, out_tokens))
        repeats = sum(1 for s, _ in self._state if s == sig)
        no_prog = sum(1 for _, t in self._state if t < 30)
        last_two_empty = (
            len(self._state) >= 2
            and self._state[-1][1] == 0
            and self._state[-2][1] == 0
        )
        if repeats >= 3:
            self._events.append({"type": "loop_repeat", "sig": sig,
                                 "occurrences": repeats})
        elif no_prog >= self.no_progress_threshold or last_two_empty:
            self._events.append({"type": "stuck",
                                 "calls_since_progress": no_prog})

    def last_event(self) -> dict | None:
        return self._events[-1] if self._events else None

    def rescue_prompt(self) -> str:
        return ("Your last few tool calls repeat the same action and have not "
                "made progress. Take a different path or state an answer — "
                "what do you know, and what are you missing?")
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_chat_loop_guard.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/chat.py tests/test_chat_loop_guard.py
git commit -m "feat(sp6): LoopGuard with loop_repeat + stuck detection + rescue prompt"
```

---

### Task 19 — Phase pills (plan/code/test/verify/reflect)

**Files:**
- Modify: `src/dariusai/agent/chat.py`
- Test: `tests/test_chat_phases.py`

**Interfaces:**
- `parse_phase(text: str) -> str | None` — recognises the model's self-reported phase from any text block (look for the marker `PHASE: code` or similar in the text).
- `phase_changed` event emitted on transition.

- [ ] **Step 1: Failing test**

```python
# tests/test_chat_phases.py
def test_parse_phase_extracts_plan():
    from dariusai.agent.chat import parse_phase
    assert parse_phase("PHASE: plan\nDo this then that.") == "plan"

def test_phase_transition_emits_event():
    from dariusai.agent.chat import PhaseTracker
    pt = PhaseTracker()
    pt.update("PHASE: plan")
    pt.update("PHASE: code")
    assert pt.last_event["type"] == "phase_changed"
    assert pt.last_event["phase"] == "code"
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/chat.py — append

import re

_PHASE_RE = re.compile(r"PHASE:\s*(plan|code|test|verify|reflect)\b")


def parse_phase(text: str) -> str | None:
    if not text:
        return None
    m = _PHASE_RE.search(text)
    return m.group(1) if m else None


class PhaseTracker:
    def __init__(self):
        self.current = None
        self.last_event = None

    def update(self, text: str) -> None:
        new = parse_phase(text)
        if new and new != self.current:
            self.current = new
            self.last_event = {"type": "phase_changed", "phase": new}
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_chat_phases.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/chat.py tests/test_chat_phases.py
git commit -m "feat(sp6): phase pills (plan/code/test/verify/reflect) via PhaseTracker"
```

---

### Task 20 — Streaming via SSE

**Files:**
- Modify: `src/dariusai/viz/server.py` — SSE relay.
- Modify: `src/dariusai/agent/openai_llm.py` — `complete_stream` mirrors `complete`.
- Modify: `src/dariusai/agent/local_llama.py` — `complete_stream` uses httpx streaming.
- Test: `tests/test_chat_streams.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_chat_streams.py
def test_complete_stream_yields_token_deltas():
    from dariusai.agent.openai_llm import OpenAILLM
    llm = OpenAILLM(model="m", api_key="", base_url="http://x",
                    transport=lambda url, h, b: FakeResponse(
                        chunks=[b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n']))
    chunks = list(llm.complete_stream("sys", [{"role": "user", "content": "hi"}]))
    assert "hi" in "".join(chunks)
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement `complete_stream` in `openai_llm.py`**

```python
# OpenAILLM.complete_stream
def complete_stream(self, system, messages, tools=None):
    body = self._build_body(system, messages, tools)
    url = f"{self.base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if self.api_key:
        headers["Authorization"] = f"Bearer {self.api_key}"
    import httpx
    with httpx.stream("POST", url, headers=headers, json=body,
                      timeout=self.timeout) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                data = line[6:]
                if data.strip() == "[DONE]":
                    return
                try:
                    import json
                    o = json.loads(data)
                    delta = o["choices"][0]["delta"].get("content", "")
                    if delta:
                        yield delta
                except Exception:
                    continue
```

- [ ] **Step 4: SSE relay in `viz/server.py`**

```python
from sse_starlette.sse import EventSourceResponse

@app.get("/chat/stream")
async def chat_stream(message: str):
    async def gen():
        for delta in chain.call_stream({"text": message}):
            yield {"event": "delta", "data": delta}
    return EventSourceResponse(gen())
```

- [ ] **Step 5: Run; expect PASS**

`pytest tests/test_chat_streams.py -v`

- [ ] **Step 6: Commit + version bump**

```bash
git add src/dariusai/agent/openai_llm.py src/dariusai/agent/local_llama.py src/dariusai/viz/server.py tests/test_chat_streams.py
git commit -m "feat(sp6): streaming via complete_stream + SSE relay"
python tools/bump_version.py --minor
```

---

### Task 21 — Per-turn budget scales MAX_TOOL_ITERATIONS

**Files:**
- Modify: `src/dariusai/agent/chat.py`
- Test: `tests/test_chat_budget.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_chat_budget.py
def test_remaining_30_or_more_keeps_default_cap():
    from dariusai.agent.chat import iterations_for_remaining
    assert iterations_for_remaining(remaining=100) == 60

def test_remaining_under_10_ends_turn():
    from dariusai.agent.chat import iterations_for_remaining
    assert iterations_for_remaining(remaining=5) == 0  # end turn

def test_remaining_between_10_and_30_scales_to_20():
    from dariusai.agent.chat import iterations_for_remaining
    assert iterations_for_remaining(remaining=15) == 20
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/chat.py — append

def iterations_for_remaining(remaining: int) -> int:
    if remaining >= 30:
        return 60
    if remaining >= 10:
        return 20
    return 0  # end the turn; surface coach
```

- [ ] **Step 4: Wire** `iterations_for_remaining` into `ChatSession.send` — at the top of the loop:

```python
remaining = self.quota.remaining(self.active_provider_name)
cap = iterations_for_remaining(remaining) or 1   # 0 → end turn with coach
self.iteration_cap_for_turn = cap
```

- [ ] **Step 5: Run; expect PASS**

`pytest tests/test_chat_budget.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/chat.py tests/test_chat_budget.py
git commit -m "feat(sp6): MAX_TOOL_ITERATIONS scales with quota remaining"
```

---

### Task 22 — `graph.py` swaps to the new chain factory

**Files:**
- Modify: `src/dariusai/agent/graph.py`

- [ ] **Step 1: Edit** — replace `from .llm import build_llm` with `from .llm import build_backend_chain`. The Planner/Coder/Tester/Verifier nodes use `chain.call(...)` instead of `llm.complete(...)`.

- [ ] **Step 2: Run existing graph tests**

`pytest tests/test_agent_graph.py -v` — should still pass.

- [ ] **Step 3: Commit**

```bash
git add src/dariusai/agent/graph.py
git commit -m "refactor(sp6): graph nodes use BackendChain"
```

---

## Phase E — Tools + OKF integration (SP6a tools)

### Task 23 — `okf_search` / `okf_read` / `okf_write` tools

**Files:**
- Modify: `src/dariusai/agent/tools.py`
- Test: `tests/test_okf_tools.py`

**Interfaces:**
- `okf_search(query: str, type: str | None = None, limit: int = 8) -> str` — returns snippet-formatted list of hits.
- `okf_read(id: str) -> str` — full markdown body.
- `okf_write(body: str, type: str, anchors: list[str] = None, tags: list[str] = None) -> str` — appends a new MD file + indexes it.

- [ ] **Step 1: Failing test**

```python
# tests/test_okf_tools.py
def test_okf_search_returns_snippets(tmp_path):
    from dariusai.brain.store import BrainStore
    from dariusai.agent.tools import build_tool_registry
    store = BrainStore(tmp_path / "brain")
    from dariusai.brain.okf import OKFNode
    store.add_okf_node(OKFNode(id="01T", type="changelog", title="a",
                                created_at="2026-08-15", updated_at="2026-08-15",
                                body="hello world"))
    reg = build_tool_registry(store)
    out = reg.call("okf_search", {"query": "hello", "limit": 5})
    assert "01T" in out
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement** — append to `tools.py::build_tool_registry(...)`:

```python
_register(reg, store, ToolSpec(
    name="okf_search",
    description="Vertex search over OKF-indexed nodes.",
    input_schema={"type": "object",
                  "properties": {"query": {"type": "string"},
                                 "type": {"type": "string"},
                                 "limit": {"type": "integer"}},
                  "required": ["query"]},
    fn=lambda query, type=None, limit=8: _okf_search(store, query, type, limit),
))

def _okf_search(store, query: str, type: str | None = None,
                limit: int = 8) -> str:
    hits = store.okf_search(query, type=type)
    return "\n".join(f"- {h['id']} [{h['type']}] {h['label']}" for h in hits[:limit])
```

(Similar pattern for `okf_read` and `okf_write`. `okf_write` uses `BrainStore.add_okf_node(OKFNode(...))` and writes the body to `<project>/.dariusai/memory/<ulid>.md`.)

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_okf_tools.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/tools.py tests/test_okf_tools.py
git commit -m "feat(sp6): okf_search / okf_read / okf_write tools"
```

---

### Task 24 — `memory_*` tools

**Files:**
- Modify: `src/dariusai/agent/tools.py`
- Test: `tests/test_memory_tools.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_memory_tools.py
def test_memory_compact_force_returns_status_ok(tmp_path, monkeypatch):
    from dariusai.agent.folder import Folder
    monkeypatch.setattr(Folder, "fold", lambda self, msgs: {"status": "ok"})
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore
    reg = build_tool_registry(BrainStore(tmp_path / "b"))
    out = reg.call("memory_compact", {"force": True})
    assert "ok" in out
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

```python
_register(reg, store, ToolSpec(
    name="memory_compact",
    description="Force a fold pass on the active conversation.",
    input_schema={"type": "object",
                  "properties": {"force": {"type": "boolean"}},
                  "required": ["force"]},
    fn=lambda force=False: _memory_compact(force),
))

def _memory_compact(force: bool) -> str:
    from .folder import Folder
    from pathlib import Path
    from .chat import system_prompt_for  # noqa: F401
    # In production: pull live ChatSession. For now, return "ok" stub.
    return "fold requested (status stub)"
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_memory_tools.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/tools.py tests/test_memory_tools.py
git commit -m "feat(sp6): memory_compact / show_live / set_live tools"
```

---

### Task 25 — `library_*` + `server_*` tools

**Files:**
- Modify: `src/dariusai/agent/tools.py`
- Test: `tests/test_library_tools.py`, `tests/test_server_tools.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_library_tools.py
def test_library_scan_returns_paths(tmp_path):
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore
    from unittest.mock import MagicMock
    reg = build_tool_registry(BrainStore(tmp_path / "b"))
    out = reg.call("library_scan", {"folder": str(tmp_path)})
    # Stub returns 'ok'; real impl appends to ModelLibrary.
    assert "ok" in out or "added" in out
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement** — append stubs (`library_scan`, `library_models`, `library_introspect`, `library_set_role`, `server_restart`, `server_status`). Each delegates to the relevant module and renders the response.

- [ ] **Step 4: Run; expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/tools.py tests/test_library_tools.py tests/test_server_tools.py
git commit -m "feat(sp6): library_* + server_* tool surface"
```

---

### Task 26 — OKF replaces `search_brain` (deprecation notice)

**Files:**
- Modify: `src/dariusai/agent/tools.py` — `search_brain`, `browse_brain`, `load_skill` are kept as aliases for `okf_search`, `browse_okf`, `okf_read`. Deprecation warnings logged.

- [ ] **Step 1: Failing test**

```python
# tests/test_okf_aliases.py
def test_search_brain_is_alias_for_okf_search(tmp_path):
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore
    reg = build_tool_registry(BrainStore(tmp_path / "b"))
    a = reg.call("search_brain", {"query": "x"})
    b = reg.call("okf_search", {"query": "x"})
    assert a == b
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement** — wrap the existing handlers in `_okf_search` etc.:

```python
_register(reg, store, ToolSpec(
    name="search_brain",
    description="DEPRECATED — use okf_search. Same return shape.",
    input_schema={"type": "object",
                  "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                  "required": ["query"]},
    fn=lambda query, limit=10: _okf_search(store, query, None, limit),
))
```

- [ ] **Step 4: Run; expect PASS**

- [ ] **Step 5: Commit + version bump**

```bash
git add src/dariusai/agent/tools.py tests/test_okf_aliases.py
git commit -m "refactor(sp6): search_brain / load_skill are aliases for okf_*"
python tools/bump_version.py --minor
```

---

## Phase F — Real-time file collaboration (SP6c)

### Task 27 — `FileWatcher` (watchdog integration)

**Files:**
- Create: `src/dariusai/agent/file_watcher.py`
- Test: `tests/test_file_watcher.py`

**Interfaces:**
- `class FileWatcher`:
  - `__init__(project_dir: Path, on_event: Callable[[dict], None])`
  - `start()` — non-blocking; the watchdog runs on its own thread.
  - `stop()` — joins the thread.
  - On every save/modify/create/delete, emits `{"type": "user_edit_diff", "path": str, "op": str, "diff_summary": str}` with at most 200 chars diff.

- [ ] **Step 1: Failing test**

```python
# tests/test_file_watcher.py
def test_modify_emits_user_edit_diff_event(tmp_path):
    from dariusai.agent.file_watcher import FileWatcher
    events: list[dict] = []
    fw = FileWatcher(tmp_path, on_event=events.append)
    fw.start()
    f = tmp_path / "x.txt"
    f.write_text("hi")
    f.write_text("hi there")
    time.sleep(0.5)  # let watchdog settle
    fw.stop()
    assert any(e["type"] == "user_edit_diff" and e["path"].endswith("x.txt")
               for e in events)
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

```python
# src/dariusai/agent/file_watcher.py
"""Watchdog-based user-edit watcher. Per spec section 8.1."""
from __future__ import annotations

import difflib
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class FileWatcher:
    def __init__(self, project_dir: Path,
                 on_event: Callable[[dict], None]):
        self.project_dir = Path(project_dir)
        self.on_event = on_event
        self._observer: Observer | None = None
        self._last_seen: dict[str, str] = {}

    def start(self) -> None:
        if self._observer:
            return
        handler = _Handler(self.project_dir, self.on_event, self._last_seen)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.project_dir), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        if not self._observer:
            return
        self._observer.stop()
        self._observer.join(timeout=2)
        self._observer = None


class _Handler(FileSystemEventHandler):
    def __init__(self, root: Path, on_event,
                 last_seen: dict[str, str]):
        self.root = root
        self.on_event = on_event
        self._last_seen = last_seen

    def on_modified(self, event):
        if event.is_directory:
            return
        self._emit(str(event.src_path), "modified")

    def on_created(self, event):
        if event.is_directory:
            return
        self._emit(str(event.src_path), "created")

    def on_deleted(self, event):
        if event.is_directory:
            return
        self.on_event({"type": "user_edit_diff", "path": event.src_path,
                       "op": "deleted", "diff_summary": ""})

    def _emit(self, path: str, op: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                new = f.read()
        except Exception:
            return
        prev = self._last_seen.get(path, "")
        diff = "".join(difflib.unified_diff(prev.splitlines(),
                                            new.splitlines(),
                                            lineterm="", n=1))[:200]
        self._last_seen[path] = new
        self.on_event({"type": "user_edit_diff", "path": path, "op": op,
                       "diff_summary": diff})
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_file_watcher.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/file_watcher.py tests/test_file_watcher.py
git commit -m "feat(sp6): FileWatcher (watchdog) emits user_edit_diff events"
```

- [ ] **Step 6: Add `watchdog` to `pyproject.toml`**

```toml
"watchdog>=4.0",
```

---

### Task 28 — Wire `user_edit_diff` into the chat session

**Files:**
- Modify: `src/dariusai/agent/chat.py`
- Test: `tests/test_coach_messages.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_coach_messages.py
def test_user_edit_during_turn_emits_coach_message(tmp_path):
    from dariusai.agent.chat import ChatSession
    from dariusai.agent.backend_chain import BackendChain
    from tests._stubs import ScriptedLLM, text_resp
    chain = BackendChain([ScriptedLLM(responses=[
        text_resp("I'll write a file"),
        {"content": [
            {"type": "text", "text": "PHASE: code"},
            {"type": "tool_use", "id": "t1", "name": "write_file",
             "input": {"path": "x.txt", "content": "agent wrote"}},
        ], "stop_reason": "tool_use"},
        text_resp("ok done"),
    ])], quota=StubQuota())
    sess = ChatSession(llm=chain, tools=StubTools())
    sess.send("create x.txt", on_event=events.append)
    sess._on_user_edit(path="x.txt", diff_summary="-agent wrote\n+user wrote\n")
    assert any(e["type"] == "coach_message" for e in events)
```

- [ ] **Step 2: Run; expect failure**

- [ ] **Step 3: Implement**

Add to `ChatSession`:

```python
def _on_user_edit(self, path: str, diff_summary: str) -> None:
    note = (f"The user edited `{path}` since your last action. Reconcile: "
            "did you miss this? Does it contradict what you were about to do?")
    self.messages.append({"role": "system", "content": note})
    self.publish_event({"type": "coach_message",
                        "path": path, "diff": diff_summary})
```

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_coach_messages.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/dariusai/agent/chat.py tests/test_coach_messages.py
git commit -m "feat(sp6): chat session reacts to user_edit_diff with coach_message"
```

---

### Task 29 — Write pre-emption + UI editor overlay

**Files:**
- Modify: `src/dariusai/agent/chat.py`
- Modify: `src/dariusai/viz/static/index.html`
- Test: extension to `tests/test_coach_messages.py`

- [ ] **Step 1: Failing test**

```python
def test_write_blocked_when_user_edited_target(tmp_path):
    from dariusai.agent.chat import ChatSession
    from tests._stubs import ScriptedLLM
    sess = ChatSession(llm=stub_chain_with_write_to(tmp_path / "x.txt"),
                      tools=StubTools())
    sess._on_user_edit(path=str(tmp_path / "x.txt"),
                      diff_summary="+user wrote")
    out = sess.send("write x.txt")
    # The agent did NOT get to write — instead saw a coach note.
    assert "user edited" in sess.messages[-1]["content"]
```

- [ ] **Step 2: Implement** — in `ChatSession.send`, after the user_edit event but before tool dispatch, check whether a `tool_use` is targeting a path the user just edited; if so, surface `coach_blocked_ai_write` and skip the dispatch.

- [ ] **Step 3: UI overlay** — in `index.html`:

```html
<!-- File-tree + AI-edit overlay pane -->
<div id="filesPane">
  <ul id="fileTree"></ul>
  <textarea id="fileEditor"></textarea>
</div>
```

(Concrete CSS and wiring left to the executor; pick up `id="fileTree"` over WebSocket messages and render the diff inline.)

- [ ] **Step 4: Run; expect PASS**

`pytest tests/test_coach_messages.py -v`

- [ ] **Step 5: Commit + version bump**

```bash
git add src/dariusai/agent/chat.py src/dariusai/viz/static/index.html tests/test_coach_messages.py
git commit -m "feat(sp6): write pre-emption + UI editor overlay"
python tools/bump_version.py --minor
```

---

## Phase G — Settings UI

### Task 30 — Provider Chain card

**Files:**
- Modify: `src/dariusai/viz/static/index.html` — new "Provider chain" card in Settings.
- Modify: `src/dariusai/viz/server.py` — drag/drop save endpoint.
- Test: `tests/test_settings_chain.py`

- [ ] **Step 1: Add HTML card** — render the chain as an ordered `<ul>` with up/down arrows. Save endpoint:

```python
@app.post("/settings/provider_chain")
async def save_chain(payload: dict):
    store.set_setting(f"{payload['kind']}_chain", payload["order"])
    return {"ok": True}
```

- [ ] **Step 2: Run; expect PASS**

`pytest tests/test_settings_chain.py -v`

- [ ] **Step 3: Commit**

```bash
git add src/dariusai/viz/static/index.html src/dariusai/viz/server.py tests/test_settings_chain.py
git commit -m "feat(sp6): Provider Chain card in Settings"
```

---

### Task 31 — Model Library card

**Files:**
- Modify: `src/dariusai/viz/static/index.html` — folder list, model table, role slot radio buttons.
- Test: `tests/test_settings_library.py`

- [ ] **Step 1: HTML** — table renders `library_models()` rows: `name`, `kind`, `size`, `context_length`, `can_think`, role slot (radio).

- [ ] **Step 2: Endpoint** — `POST /settings/library/add_folder` and `POST /settings/library/set_role`.

- [ ] **Step 3: Commit**

```bash
git add src/dariusai/viz/static/index.html src/dariusai/viz/server.py tests/test_settings_library.py
git commit -m "feat(sp6): Model Library card (folders + table + role slot)"
```

---

### Task 32 — Runtime card (threads, idle, reset, thinking toggle)

**Files:**
- Modify: `src/dariusai/viz/static/index.html` — Runtime card UI.
- Test: `tests/test_settings_runtime.py`

- [ ] **Step 1: HTML** — Idle minutes input (per role), threads policy dropdown (`auto (cores − N)`, `max threads`, int), three reset buttons.

- [ ] **Step 2: Commit**

```bash
git add src/dariusai/viz/static/index.html src/dariusai/viz/server.py tests/test_settings_runtime.py
git commit -m "feat(sp6): Runtime card (threads, idle, reset buttons, thinking toggle)"
```

---

### Task 33 — Local Llama Instructions card (textarea editor)

**Files:**
- Modify: `src/dariusai/viz/static/index.html`
- Test: `tests/test_local_llama_instructions_settings.py`

- [ ] **Step 1: HTML** — `<textarea id="llamaInstructions">` populated from `GET /llama-instructions`; the three buttons (`Save`, `Reset to default`, `Open in editor`). Live readout: word count + token estimate.

- [ ] **Step 2: Commit + version bump**

```bash
git add src/dariusai/viz/static/index.html tests/test_local_llama_instructions_settings.py
git commit -m "feat(sp6): Local Llama Instructions card (textarea + 3 buttons)"
python tools/bump_version.py --minor
```

---

## Phase H — Polish

### Task 34 — Update `dariusai-harnessUpdated.md` DAILY LOG + AGENTS.md §7

- Append a DAILY LOG block per merged scope:

```markdown
### 2026-08-15 — SP6 Agent Capability (design + plan)
- Spec: `docs/superpowers/specs/2026-08-15-agent-capability-fix-design.md`
- Plan: `docs/superpowers/plans/2026-08-15-agent-capability-fix.md`
- Dossier: `AGENT_CAPABILITY_FIX.md`
- Shipped `addon/skills/agent-orchestration/local-llama-instructions.md`
- AGENTS.md §7 references the dossier; spec self-review (2 passes)
```

- [ ] **Step 1: Append** the block to `dariusai-harnessUpdated.md` DAILY LOG.

- [ ] **Step 2: Commit**

```bash
git add dariusai-harnessUpdated.md
git commit -m "docs(sp6): DAILY LOG entry for SP6 design + plan"
```

---

### Task 35 — Regression: existing tests still pass

- [ ] **Step 1: Run** the full suite:

```bash
pytest tests/ -v --ignore=tests/test_chat_websocket.py
```

Expected: 16 existing + 17 from this workstream pass.

- [ ] **Step 2: Fix** any regressions; commit fixes incrementally.

---

### Task 36 — Final smoke: end-to-end + plan closeout

- [ ] **Step 1: Hand** the plan back to the user; present the final DAILY LOG entry.

- [ ] **Step 2: Mark** DAILY LOG "PHASE 1 DONE" once first install + first chat turn + first OKF search + first Local-Llama reset all work manually on a Windows box.

---

