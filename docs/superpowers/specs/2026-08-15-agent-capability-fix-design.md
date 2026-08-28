# Agent Capability Fix — Design

**Sub-project**: SP6 — Agent Capability (overhaul of the AI layer)
**Status**: DESIGN — awaiting user approval
**Created**: 2026-08-15
**Owner plan**: `dariusai-harnessUpdated.md` (DAILY LOG + DONE/TODO)
**Depth dossier**: `AGENT_CAPABILITY_FIX.md` (project root, mirrors `BrainFIX.md`)
**Companion plan** (next, after approval): `docs/superpowers/plans/2026-08-15-agent-capability-fix.md`

---

## 0. Mission

Replace the current chat-centric AI layer with a **full agent + live-editor system** where:

1. The agent carries a **file-based, OKF-indexed memory** instead of pushing the full conversation history on every provider call.
2. **Local llama.cpp is the offline-capable primary runtime** for both chat and embedding; providers are the online upgrade.
3. **The agent + the user can edit the same files live**: the agent sees manual edits and coaches in-chat; the user sees agent edits in an editor overlay.
4. The provider chain (minimax.io → agnes-ai → NVIDIA free → local GGUF) is **automatic**, quota-aware, and CPU-only at every local step.
5. The harness stays usable — **loop detection, stuck awareness, visible phases, streaming output** — even when the active model is small.

---

## 1. Findings — what the AI layer does today, measured

### F1 — Full conversation history is sent on every iteration

`src/dariusai/agent/chat.py:128` calls `self.llm.complete(system=..., messages=self.messages, tools=tool_schemas)` where `self.messages` is the **entire** conversation history plus the per-iteration tool results. The chat session never trims or summarises mid-loop; the only compression is auto-compaction at 75% of the context window (`compact_threshold_ratio=0.75`, `chat.py:77`). For a 200k-token window that means the first 150k tokens are sent on **every** iteration of a long turn — and `MAX_TOOL_ITERATIONS=60` (chat.py:29) can fire lots of them.

Net effect: provider cost grows non-linearly with turn depth, and the per-iteration latency is wasted on already-seen tokens.

### F2 — Doctrine is paid for on every iteration

`src/dariusai/agent/doctrine.py` ships ~5–10k tokens of system prompt (the full `SUPERPOWERS_BOOTSTRAP` + ARCHITECTURE + RULES + KNOWLEDGE, spliced at `with_doctrine(...)`). `chat.py:31` calls `with_doctrine(...)` once at session construction and sends the same string on **every** LLM call for the session's lifetime. A 60-iteration turn multiplies doctrine cost by 60.

### F3 — Tool registry prioritises whatever comes first

`src/dariusai/agent/tools.py:421–608` registers 14 tools. They appear to the model in registration order: `read_file, write_file, list_dir, run_shell, web_research, search_brain, browse_brain, list_projects, project_types, create_project, load_skill, set_todos, invoke_skill, learn_skill`. There is no priority hint, no "use this first", no "rare" warning. A small model picks the first plausible tool and rarely retries.

### F4 — No loop detection in the chat loop

The chat loop terminates on `(a) assistant emits no tool_use` or `(b) iteration cap`. There is no detection of repeated identical tool calls, no no-progress heuristic, no empty-output detector. The cap is reached silently and the loop emits a "Paused after N calls — continue" message (chat.py:189–195). A model that calls `read_file(same_path)` 60 times drives the loop all the way to the cap.

### F5 — Provider routing is rigid

`src/dariusai/agent/llm.py:build_llm` returns one of two clients (`AnthropicLLM`, `OpenAILLM`) chosen by a single provider's protocol. There is no chain, no fallback, no health probe, no offline-detection, no per-session budget awareness. If the active provider is rate-limited or offline, the chat panel surfaces a provider error and the loop aborts.

### F6 — llama.cpp is not wired at all

There is no local runtime. The harness has no concept of "offline". The active provider is a hard dependency.

### F7 — No manual-edit-to-agent feedback loop

There is a `src/dariusai/viz/static/index.html` chat panel + a 3D viz + a slash-command picker. There is no file watcher in `src/dariusai/viz/server.py`. If the user edits a file mid-turn, the agent does not see the edit and cannot coach.

### F8 — Brain indexes skills, not free-form MD files

`src/dariusai/brain/store.py` is a SQLite store over `nodes`/`edges`. Skills imported by `omni_import.py` come from `addon/skills/<group>/<name>/SKILL.md`. There is no facility for free-form Markdown files (project memory, change logs, agent notes) to be indexed as OKF knowledge nodes.

### F9 — No vector search

`src/dariusai/brain/store.py:196–197` (legacy store.search) matches on `label` and `tags` only. Even after the FTS5 rewrite in `BrainFIX.md` Phase 3, search is keyword-based. There is no embedding-driven "find anything that means the same thing as this query" path.

---

## 2. Reframing per the user

This sub-project is the user's vision, not a defect fix. The relevant reframings (all from the user, 2026-08-15):

- **No small-model-first carve-out.** Initial work targets full-power providers (max context). The local llama.cpp default is a *runtime* for offline use, not an excuse to degrade the agent's UX for small models. The chat model picks strong defaults; the embed model can stay small.
- **Maximum context from providers.** When online, use the provider's advertised max (200k for Anthropic-class, 1M for GPT-4.1, whatever minimax.io exposes).
- **All-the-time coding knowledge.** The doctrine, the skills, and the OKF-indexed knowledge base are all live in the prompt and index, not lazy.
- **Manual-edit coaching.** The user and the agent edit the same files. The agent sees diffs and emits coaching messages in chat.
- **Local-first.** llama.cpp is the **primary** offline runtime; online providers are a *boost*, not a requirement.
- **Provider chain.** minimax.io → agnes-ai → NVIDIA free endpoints → local GGUF. Auto-fallback. Quota-aware. CPU-only.
- **Concurrency.** Up to 4 chat sessions in parallel; quotas tracked globally per-provider.

The design below honours all of the above. Some of the original "small-model" rationale is retained where it applies (loop detection, stuck path, terse tool descriptions) because it helps any model — strong or small.

---

## 3. Sub-projects

| id | name | files | depends on |
|---|---|---|---|
| **SP6a-embed** | Local GGUF runtime + auto-download + multi-folder model library + reset buttons + GGUF introspection + thinking-detection + Local-Llama instructions override | `src/dariusai/agent/local_llama.py` (NEW), `src/dariusai/agent/llama_manager.py` (NEW), `src/dariusai/agent/model_library.py` (NEW), `src/dariusai/agent/embedder_manager.py` (rewritten), `addon/skills/agent-orchestration/local-llama-instructions.md` (NEW shipped default) | root |
| **SP6a** | File-based memory + OKF + BackendChain + QuotaTracker | `src/dariusai/brain/okf.py` (NEW), `src/dariusai/brain/vector_index.py` (NEW), `src/dariusai/brain/store.py` (OKF index table), `src/dariusai/agent/backend_chain.py` (NEW), `src/dariusai/agent/quota_tracker.py` (NEW), `src/dariusai/agent/llm.py` (factory swap), `src/dariusai/agent/openai_llm.py` (online + local clients), `src/dariusai/model_catalog.py` (chain spec), `src/dariusai/viz/server.py` (status endpoint), `src/dariusai/viz/static/index.html` (status badge) | depends on **SP6a-embed** |
| **SP6b** | Compact-prompt chat loop (phases, loop/stuck, streaming, per-turn budget) | `src/dariusai/agent/chat.py` (rewrite), `src/dariusai/agent/doctrine.py` (system-prompt composition), `src/dariusai/agent/graph.py` (LLM factory swap) | depends on SP6a |
| **SP6c** | Real-time file collaboration (manual-edit watching + coaching) | `src/dariusai/agent/file_watcher.py` (NEW), `src/dariusai/viz/server.py` (WS bridge), `src/dariusai/viz/static/index.html` (coach UI) | depends on SP6b |
| (deferred) SP6d | Multi-agent orchestration inside one task | `src/dariusai/agent/multi_agent.py` (NEW), `src/dariusai/agent/graph.py` (parallel branches) | separate design |
| (deferred) SP6e | Editor UX surface (code-edit panel, file tree, diff overlays) | (separate design) | depends on SP6b + SP6c |

First round ships **SP6a-embed + SP6a + SP6b + SP6c** as one cohesive sub-project. SP6d / SP6e only if explicitly requested later.

---

## 4. Architecture overview

```
┌───────────────────────────────────────────────────────────────────────────┐
│                                  UI                                        │
│  ┌──────────────┐  ┌──────────────────────┐  ┌──────────────────────────┐  │
│  │  Chat panel  │  │  Phase pills         │  │  Provider-status badge   │  │
│  │  (streamed)  │  │  plan→code→test→     │  │  minimax.io · 12 / 4k    │  │
│  │              │  │   verify→reflect      │  │  llama-server:online     │  │
│  └──────────────┘  └──────────────────────┘  └──────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  Files panel — AI diff overlay (SP6c) | file tree | manual-edit tick │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
                                  │ WebSocket events
                                  ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                              Agent runtime                                 │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  ChatSession.send(text)                                               │ │
│  │    1. Build compact prompt (doctrine + active task + recent_summary + │ │
│  │       live_version + top-K OKF hits)                                  │ │
│  │    2. Stream tool-calling loop (phases, loop/stuck guard, budget)     │ │
│  │    3. After turn: append ChangeLog-<ulid>.md, fold pass every N turns │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                          │                                                 │
│                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  LLM Factory  ──►  BackendChain (chat)  ──►  Backend tier (returns    │ │
│  │                                                 Anthropic/OpenAI    │ │
│  │                                                 blocks)              │ │
│  │   tiers (chat): minimax.io → agnes-ai → NVIDIA-free → local GGUF     │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  EmbedderManager  ──►  BackendChain (embed)                           │ │
│  │   tiers (embed): agnes-ai → minimax.io → NVIDIA-free → local GGUF     │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  QuotaTracker (per-provider counters; pre-call gate; post-call hooks) │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
│                          │                                                 │
│                          ▼                                                 │
│  ┌──────────────────────────────────────────────────────────────────────┐ │
│  │  OKF indexer (src/dariusai/brain/okf.py)                              │ │
│  │   Source of truth: <project>/.dariusai/memory/*.md                    │ │
│  │   Secondary: BrainStore (SQLite) — id, type, anchors, tags,           │ │
│  │              embedding_ref                                            │ │
│  │   Tertiary: vector_index.npy — float32 embeddings for vertex search   │ │
│  └──────────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          External runtimes                                 │
│  ┌────────────────────────────┐    ┌─────────────────────────────────┐    │
│  │  llm providers             │    │  llama-server (CPU-only)        │    │
│  │  (minimax.io, agnes-ai,    │    │  ┌─────────────┬────────────┐  │    │
│  │   NVIDIA free, etc.)       │    │  │ chat :7788  │ embed :7789│  │    │
│  │                             │    │  │ code-instr  │ nomic-Q4   │  │    │
│  └────────────────────────────┘    │  └─────────────┴────────────┘  │    │
│                                      └─────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 5. SP6a-embed — Local GGUF runtime + auto-download

### 5.1 Two-process llama.cpp architecture

Two `llama-server.exe` processes, managed independently:

| role | default model | port | flags |
|---|---|---|---|
| chat | `bartowski/Qwen2.5-Coder-7B-Instruct-GGUF`, file `Qwen2.5-Coder-7B-Instruct.Q4_K_M.gguf` (~4.5 GB) | **7788** | `--jinja`, `-c 8192`, `--threads <auto>` |
| embed | `nomic-ai/nomic-embed-text-v1.5-GGUF`, file `nomic-embed-text-v1.5.Q4_K_M.gguf` (~80 MB) | **7789** | `--embedding`, `-c 2048`, `--batch-size 512`, `--threads <auto>` |

Both download the *same* `llama-server.exe` binary from a pinned `llama.cpp` GitHub release (one ~30 MB download total). Pin the release at design-finalisation (planned: `b4500` or current stable at coding time, SHA-256 verified against the release manifest). Models downloaded to `%LOCALAPPDATA%\DariusAI\models\`. Binary to `%LOCALAPPDATA%\DariusAI\bin\`.

### 5.2 Thread policy

- Default: `threads = max(4, os.cpu_count() - 8)`, clamped to `[4, os.cpu_count()]`. Rationale: leaves 8 cores for OS + UI on big boxes; on a 4-core laptop the `−8` floor of 4 kicks in.
- Settings page exposes: `auto (cores − N)` with N configurable (default 8), `max threads`, or an explicit integer.
- Same flag, applied independently to each `llama-server.exe` spawn.

### 5.3 CPU-only (locked decision)

- Binary pin: `llama-<ver>-bin-win-cpu-x64.zip`.
- Never `--n-gpu-layers`. Never `-ngl`. No CUDA / cuBLAS / cuDLL runtime dependencies.
- Settings page does **not** expose a GPU toggle. CPU-only is permanent.

### 5.4 Idle shutdown

- Default `EMBED_IDLE_SHUTDOWN_MINUTES=10` for the embed server.
- Default `CHAT_IDLE_SHUTDOWN_MINUTES=10` for the chat server.
- Per-server override in Settings.
- App-exit hook: kill both servers, clean pid-files.

### 5.6 Model library — multi-folder, GGUF-introspected

The harness keeps a **`ModelLibrary`** of every GGUF model the user has pointed at. The default offline cache (`%LOCALAPPDATA%\DariusAI\models\`) is registered automatically; users add more folders — other SSDs, drives, `D:\llms\`, `E:\models\`, etc. — from Settings.

```python
ModelLibrary
  add_folder(path)            # scan for .gguf; metadata extraction once per file (cached by sha256)
  remove_folder(path)
  rescan(folder=None)         # folder=None = rescan every registered folder
  list_folders() -> [path]
  list_models(kind=None) -> [ModelEntry]
  introspect(path) -> ModelEntry
  set_role(model_id, role)    # role = "chat" | "embed" | None (unassign)
  active_chat_id / active_embed_id  # what's currently in each role slot
```

`ModelEntry`:

```python
{
  id: "01JAZZ...",                     # ULID; stable for the (path, sha256) tuple
  path: "D:/llms/qwen3-coder/Q4_K_M.gguf",
  display_name: "qwen3-coder-Q4_K_M",
  size_bytes: 4_530_000_000,
  sha256: "abc123...",
  kind: "chat" | "embed" | "unknown",  # detected from GGUF architecture + filename; user-overridable
  context_length: 32768,               # from GGUF *.general.context_length; null when absent
  chat_template: "<jinja string>",     # from GGUF tokenizer.chat_template; null when absent
  can_think: True,                     # heuristic; see §5.8
  architecture: "qwen3",
  detected_at: "2026-08-15T...",
}
```

**GGUF introspection.** Read a small head of each file — magic, version, and the `general.*` + `tokenizer.*` keys — without loading the full model. Required fields:

- `*.general.context_length` → `context_length`; if absent the runtime defaults to `-c 8192` (Settings-overridable).
- `tokenizer.chat_template` → `chat_template`; if absent the runtime uses `--chat-template-file` from a bundled `chat_templates/` dir or runs without an explicit template.
- `*.general.architecture` → `architecture`; also drives `kind` heuristics (LlamaArchitecture with chat heads → chat; BERT / Nomic-architecture with no chat heads → embed).

Implementation: add the small `gguf-py` PyPI dep, which is the canonical GGUF parser. Fall back to a hand-rolled struct-based header reader if the dep is missing at runtime (it isn't — gguf-py is one tiny file). Cache the parsed entry on `sha256` so re-scanning a folder with N models is free after the first pass.

**Scan triggers:** on `add_folder(path)`, on Settings "rescan all", on app startup (cheap on no-change folders), and after a `Reset all servers` action.

### 5.7 Reset server button (Settings)

The Settings "Runtime" card exposes three reset actions:

- **Reset chat server** — kills `llama-server` on port 7788, restarts with the currently-selected chat model.
- **Reset embed server** — same for port 7789.
- **Reset all** — both.

`LlamaManager.restart(role)` lifecycle:

1. SIGTERM the existing process; wait 5 s for clean exit.
2. SIGKILL if still alive.
3. Remove the pid-file.
4. Re-spawn with the same args the manager held in memory. If the chat model path changed since last spawn, the new path is used.

The chat panel renders a `server_restarting` event with `{role, old_pid, new_pid, model_name}`. The chat session briefly shows "warming llama-server…". When `/health` is back (≤ 30 s) the session resumes; if the new model fails to load the manager falls back to the default pinned model and surfaces `server_restart_failed` with the exception text.

The button exists *separately* from a model change because the running model sticks between sessions — only when the user picks a different model do they need a restart. The button makes that swap one click.

Auto-restart (without the button) happens on `set_role(model_id, role)` assignment if and only if the new role differs from what the running server is using.

### 5.8 Can-the-model-think detection

Three signals, in priority order:

1. **Chat template signature.** Jinja template contains `{%- if ... %}<think>...</think>` patterns (or equivalents used by Qwen3, DeepSeek-R1, QwQ, Phi-4-Reasoning). A small set of regexes covers the common shapes.
2. **Filename / model-card markers.** `qwen3`, `qwq`, `*-think*`, `deepseek-r1`, `*-reasoning*` — listed patterns against the filename stem.
3. **Architecture flag.** GGUF metadata contains an explicit capability flag (rare today, but reserved for the future).

A model with `can_think=True` exposes a Settings toggle **"thinking on / off"**, default **OFF**. The agent never enables reasoning unilaterally; it asks the user (via a coach message) when its task appears reasoning-heavy. The toggle writes a chat-template override that strips the `<think>` block but keeps the rest of the template intact.

The chat panel renders a small `thinking: on/off` pill in the provider-status badge when the active chat model supports it.

### 5.9 Per-role model slot

The library tracks **role assignments** independently of detection:

- **chat slot**: which ModelEntry's path is the next chat spawn's `--model` flag.
- **embed slot**: same for the embed server.

Assigning a model to a slot:

- If the slot was empty → no restart; the slot is held until the next natural spawn.
- If the running server is using a *different* model → `LlamaManager.restart(role)` runs automatically.
- Unassigning a slot → falls back to the default pinned model (§5.1) on next spawn.

The picker is keyboard-friendly (j/k to navigate, Enter to assign) so the user can swap models without touching the mouse.

### 5.10 Local-Llama instructions override

The local GGUF path gets a **user-editable system prompt override**: the user pastes their full `agents.md` content into a text box in Settings, tweaks it for the local runtime, and the harness uses it as the system prompt only when the chat slot is on local GGUF. Online backends keep the doctrine unchanged.

**Storage — file-backed, OKF-indexed:**

```
<project>/.dariusai/llama_instructions.md
```

Frontmatter:

```yaml
---
type: "agent_note"          # OKF type — visible to vertex search
title: "Local Llama Instructions"
applies_to: "local-gguf"    # backend matcher — used by the prompt builder
---
```

Body: the actual instructions in plain Markdown. File-backed (not just a Settings blob) so the user can edit it in their regular editor AND the agent can read it via `okf_read` for self-coaching ("what am I configured to do right now?") AND SP6c's file watcher sees manual edits and offers a coach message about instruction drift.

**Default content.** A streamlined llama-friendly version of the doctrine ships at:

```
addon/skills/agent-orchestration/local-llama-instructions.md
```

It's shorter, imperative, list-of-rules, no fancy prose — sized for what a Q4_K_M 7B model actually follows. The Settings page's "Reset to default" button copies this shipped file into `<project>/.dariusai/llama_instructions.md`.

**Prompt-builder rule (§7.1):**

```text
system:
    if active_backend.kind == "local" and llama_instructions_path exists:
        read the file → system prompt = (file body)
    else:
        system prompt = doctrine + memory rules + editing rules
```

When the local override is active, the chat panel renders a small badge: `"local-llama instructions · <length> chars"` so the user knows the doctrine is being replaced.

**Settings card.** New "Local llama instructions" card in the Runtime section:

- Multi-line `<textarea>` (8–10 rows, monospace, scrollable).
- Path field shows the resolved `<project>/.dariusai/llama_instructions.md` path.
- Buttons: **"Reset to default"** (copies the shipped file), **"Open in editor"** (opens the file in the SP6c editor overlay if present, or the OS default otherwise), **"Reload from disk"** (re-reads after SP6c detects external edits).
- Preview indicator: word count + token estimate + first-line preview.

**Why this matters for local GGUF specifically.** Q4_K_M quantised 7B models lose focus on long prose system prompts; the doctrine (~5–10k tokens) routinely tips them into instruction-following failure. A user-tuned `agents.md` of ~500–1500 tokens, written in imperatives the user has verified on their hardware, is dramatically more reliable. Online providers with larger contexts keep the full doctrine because they can afford it.

### 5.5 Lifecycle

```
LlamaServerManager
  start(role: "chat" | "embed")  ──►  spawn llama-server, write pid-file, wait /health
  stop(role)                      ──►  SIGTERM, wait 5s, then SIGKILL, remove pid-file
  ensure_ready(role)              ──►  start if not running; raise LlamaUnready on /health timeout
```

A `LlamaManager` instance owns both processes. Health probe at `/health` returns 200 when `/v1/chat/completions` or `/v1/embeddings` can serve a real request. Probe timeout: 30 s.

### 5.6 First-run UX

- Active provider does NOT have an embeddings endpoint → backend falls through to local GGUF.
- `LlamaManager.start("embed")` is called for the first time.
- Chat panel emits `embedding_warming` event: `{stage: "downloading" | "starting_server" | "loading_model" | "ready"}` so the user sees progress.
- Same for chat server when needed.

---

## 6. SP6a — File-based memory + OKF + BackendChain

### 6.1 Memory layout

```
<project>/.dariusai/memory/
├── FullContext.md             # append-only full history; stable timestamp anchors
├── Live_version.md            # curated "current good state" (agent may suggest; user owns)
├── ChangeLog-<ulid>.md        # one file per concrete change
└── _index/                    # gitignored
    ├── okf.sqlite             # FTS5 + nodes(id, type, tags, anchors, embedding_ref)
    └── vector_index.npy       # float32 embeddings — one row per node body
```

All MD files follow the OKF frontmatter (see 6.2). The indexer watches this directory and treats each MD as a node.

### 6.2 OKF frontmatter schema

```yaml
---
id: "01JAZZ0R8K6S9X3Y4F2V1M7Q9P"          # ULID
type: "context" | "changelog" | "live" | "skill" | "tool" | "agent_note" | "user_edit" | "decision"
title: "<one line>"
created_at: "2026-08-15T10:58:00Z"
updated_at: "2026-08-15T11:04:12Z"
anchors: ["01JAZZ0Q...", "01JAZZ0T..."]    # related ids
tags: ["auth", "oauth2", "refactor"]
embedding_ref: "vec-000123"                # row in vector_index.npy
source: "chat" | "web" | "user" | "tool"   # provenance
score_meta: { "uses": 7, "good": true }
---

# Body in Markdown (≤ 50k chars, gated)
```

- `id` is a ULID (sortable, timestamp-encoded); the same ULID is the anchor written into `FullContext.md` so `grep <ulid> FullContext.md` jumps to the original placement.
- `type` enables filtered searches. Each value has a distinct role (see 6.2.1 below).
- `embedding_ref` is a row index into `vector_index.npy`, recomputed when the body changes.
- `anchors` create a graph of relations for `browse_okf`-style traversal.

#### 6.2.1 Type roles

| type | written by | body shape | purpose |
|---|---|---|---|
| `context` | fold pass | structured summary (active task, files, decisions, state, open questions) | the recent-summary block the loop re-injects each turn |
| `changelog` | every material action | prose + before/after diff extracts | one `ChangeLog-<ulid>.md` per material change |
| `live` | user (or agent with confirmation) | the curated current state of the project | body of `Live_version.md` |
| `skill` | `omni_import` / `brain/learn` | parsed SKILL.md fields | indexed SKILL.md bodies from `addon/skills/**` |
| `tool` | `tools._ensure_tool_node` | description + best_practices | OKF mirror of each registered tool |
| `agent_note` | any tool call (free-form) | agent's reasoning / scratchpad | lightweight durable notes the agent wants to remember |
| `user_edit` | SP6c file watcher | diff summary + user attribution | trails of manual edits for the agent to coach against |
| `decision` | agent (only on explicit `decision` tool call) | one decision per node, no scope creep | high-signal design choices, easier to query than burying them in prose |

Adding a new type is open (more values can join the enum); the eight above are the v1 vocabulary. `okf_search(query, type="decision")` returns only decisions by default; default `type=None` searches all.

### 6.3 Vertex search

- **Backend:** `sqlite-vec` (sqlite extension, no extra process). Fall back to `numpy` + cosine over `vector_index.npy` if the extension is missing in the bundled SQLite.
- **Embeddings preferred:** `agnes-ai /v1/embeddings` (if a key is configured) → `minimax.io /v1/embeddings` → NVIDIA free → local GGUF embed.
- **Tool:** `okf_search(query, type=None, limit=8)` returns `[{id, type, title, score, snippet}]` sorted by cosine. The corpus = all OKF-indexed MD bodies.
- **Snippets:** the top 200 tokens of each hit's body, returned in the same call.

### 6.4 BackendChain

```
BackendChain(rank=[<Backend> x N], kind: "chat" | "embed")
  call(...)
    for backend in rank:
        if not backend.ready(): continue                  # quota exhausted or offline
        try:
            result = backend.call(...)
            record_success(backend, usage_hints)
            return result
        except QuotaExhausted as e:
            mark_exhausted(backend, until=e.retry_after)
        except BackendError as e:
            record_failure(backend, e)
            continue                                       # try next
        except TransientError as e:
            record_failure(backend, e)
            continue                                       # try next
    raise AllBackendsFailed(last_errors)
```

Each `Backend` exposes `name`, `kind`, `probe() → bool`, `ready() → bool` (checks QuotaTracker), `call(...)`, `last_error`. The chain has a hot path on the last successful tier (skips earlier `probe()` calls when they recently succeeded).

### 6.5 QuotaTracker

- **Per-provider counters** (global): `calls_used`, `calls_max`, `last_reset`, `rate_limited_until`, `last_failure_kind`.
- **Per-session counters**: the chat session's per-iteration count for the active tier.
- **Pre-call gate:** `Backend.ready()` returns False when `calls_used >= calls_max` OR `rate_limited_until > now()`.
- **Post-call update:** parse `x-ratelimit-remaining` etc. where the provider exposes headers; otherwise increment by 1.
- **404 on /v1/embeddings / /v1/chat/completions** → mark the tier offline for 5 min (don't hammer). `ready()` returns False.

### 6.6 Provider chain (defaults)

| chain | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| chat | `minimax.io` | `agnes-ai` | NVIDIA free | local GGUF (chat :7788) |
| embed | `agnes-ai` | `minimax.io` | NVIDIA free | local GGUF (embed :7789) |

Order is **configurable** in Settings (drag-and-drop in the UI). Each tier carries:

- `name` (display + log id)
- `kind` (provider, local, offline-only)
- `api_key` (DPAPI-encrypted; reused from the existing Settings providers table)
- `base_url` (with a sensible default per provider name)
- `model_id` (per tier)
- `embeddings_route` (optional — providers without one are excluded from the embed chain)
- `monthly_call_cap` (user-declared ceiling, fallback for providers that don't expose headers)

NVIDIA free defaults: `monthly_call_cap=4000`, `per_session_call_cap=32` (overridable). QuotaTracker enforces both.

### 6.7 New tools (added to the registry)

- `okf_search(query, type=None, limit=8)` — vertex + filtered search; returns ids + titles + snippets.
- `okf_read(id)` — full MD body by id.
- `okf_write(body, type, anchors=[], tags=[])` — append a new MD node; emits embedding + indexes it.
- `memory_compact(force=False)` — trigger an immediate fold pass.
- `memory_show_live()` — return `Live_version.md`.
- `memory_set_live(body)` — overwrite `Live_version.md` (rare; agent can suggest; user accepts).
- `embedding_status()` — `{backend, model_id, model_dim, server_pid, last_used}`.
- `embedding_redownload(model_id)` — drop cache, re-download a different embed model.
- `embedding_stop_server(role)` — for forced shutdown in Settings.

**Model library + server tools (NEW):**

- `library_scan(folder)` — register a folder in the ModelLibrary and run a scan; returns `{folder, models_added, models_updated}`.
- `library_models(kind=None)` — list `ModelEntry`s; `kind` filters by detected kind.
- `library_introspect(model_id)` — full `ModelEntry` for one id (path, size, context, chat_template, can_think, architecture, sha256).
- `library_set_role(model_id, role)` — assign a model to `chat` / `embed`; triggers an auto-`server_restart(role)` if the running server is on a different model.
- `server_restart(role)` — manual restart; surfaces `server_restarting` then `server_ready` or `server_restart_failed`.
- `server_status()` — `{chat_pid, embed_pid, chat_model, embed_model, chat_last_health_ok_at, embed_last_health_ok_at, library_folders}`.

### 6.8 Tools removed / repurposed

- `search_brain`, `browse_brain`, `load_skill`, `learn_skill`, `invoke_skill` → all use OKF under the hood. `invoke_skill(name)` keeps its shortcut to read SKILL.md bodies, but those bodies are also indexed as OKF nodes so they show up in vertex search.

---

## 7. SP6b — Compact-prompt chat loop

### 7.1 Per-turn prompt recipe

```text
[messages sent to provider each iteration]
  system:   full doctrine + memory rules + editing rules
  user:     "[active task — the just-pressed-Enter message]"
  assistant: "[empty / phase pill]"

  user:     "[==Recent context summary==\n<folded summary of last N turns, ≤ 1.5k tokens>]"
  user:     "[==Live state==\n<verbatim Live_version.md, ≤ 2k tokens>]"
  user:     "[==Relevant prior work==\n<top-K OKF vertex hits, K=4, body truncated to 200 tokens each>]"
  user:     "[==Recent tool results==\n<last 2-4 tool_result blocks from this turn>]"

  assistant: "[intermediate tool calls + text from earlier this turn]"
  user:     [the iteration's new tool_result blocks]
```

**No raw history** is sent. Only: doctrine, active task, recent summary, live state, top-K OKF hits, recent tool results for the current turn. Iteration count: up to `MAX_TOOL_ITERATIONS=60` (unchanged) but each iteration is cheaper because the prompt is small.

**System-prompt resolution:**

```text
system:
    if active_backend.kind == "local" and <project>/.dariusai/llama_instructions.md exists:
        system = (file body)                  # user-tuned local instructions; replace doctrine
    else:
        system = doctrine + memory rules + editing rules
```

The local-override path is what makes a Q4_K_M 7B model reliable offline. Online providers keep the full doctrine unchanged.

### 7.2 Compaction policy

The existing `ChatSession.auto_compact` field (which folds history into in-session stub turns at 75% of `context_window`) is **replaced**, not augmented. The new compaction policy writes to disk; the provider always sees the compact prompt recipe in §7.1 with no in-session stub turns.

- **Cadence:** every `MEMORY_FOLD_EVERY=8` turns, run a fold pass.
- **Backstop:** if `current_input_tokens / context_window > 0.60`, fold immediately regardless of cadence.
- **Fold pass (runs in a separate LLM call, `tools=[]`):** summarises the last 8 turns into a structured record (active task, files touched, decisions, state, open questions). Appends a single entry to `FullContext.md` with the timestamp anchor (ULID-encoded).
- **`Live_version.md` rewrite:** same fold pass, if any decision / state change is detected. User overrides are respected — the agent never overwrites a manually-edited `Live_version.md` without a coach message.
- **`ChangeLog-<ulid>.md`:** every tool call that materially affects state (write_file, create_project, shell command with side-effects) appends one. Linked by `<ulid>` to `FullContext.md`.

### 7.3 Phases

Each model response carries a phase tag (`plan`, `code`, `test`, `verify`, `reflect`). The chat panel renders pills. The chat loop tracks transitions and emits `phase_changed` events. Phase pills are pure model self-report; the chat loop does not enforce transitions (still relies on the model).

### 7.4 LoopGuard

State per ChatSession:

```python
state = deque(maxlen=8)   # last 8 (call_signature, output_tokens)
def see(call_sig: str, out_tokens: int):
    state.append((call_sig, out_tokens))
    repetitions = sum(1 for s in state if s[0] == call_sig)
    no_progress_streak = sum(1 for _, t in state if t < 30)
    if repetitions >= 3: emit loop_repeat
    # "empty" = (a) usage.output_tokens == 0, OR (b) the assistant
    # produced content blocks but none had type=="text" (only tool_use
    # calls with no narration). Either is a stuck signal under pressure.
    last_two_empty = (out_tokens == 0 or no_text_block(out)) and \
                     (prev_out_tokens == 0 or no_text_block(prev_out))
    if no_progress_streak >= 5 or last_two_empty: emit stuck
    on loop_repeat/stuck: inject a one-shot rescue prompt into the next system message
```

Rescue prompt example: *"Your last 4 calls were `read_file('src/auth.rs')`. The output hasn't changed in 6 calls. Take a different path or state an answer: what do you know and what are you missing?"*

The loop never aborts on its own; the user still owns flow control via "Continue". The rescue prompt is meant to coax the model out of its stuck state naturally.

### 7.5 Per-turn budget integration

The chat loop reads `QuotaTracker.calls_remaining(active_provider)` before each LLM call. As the ceiling approaches, the loop reduces `MAX_TOOL_ITERATIONS` for that session dynamically:

- `calls_remaining >= 30` → `MAX_TOOL_ITERATIONS = 60` (current default).
- `30 > calls_remaining >= 10` → `MAX_TOOL_ITERATIONS = 20`.
- `calls_remaining < 10` → end the turn with a "provider budget tight — switching backend" event and a coach message.

### 7.6 Streaming

- `AnthropicLLM` and `OpenAILLM` (online) gain `complete_stream(...)` that yields token deltas via SDK streaming.
- `LocalLlamaLLM` (online-shaped client pointed at llama-server :7788) uses the same protocol.
- `viz/server.py` exposes a `SSE` stream that relays deltas to the chat panel.

The chat panel renders partial assistant text from the very first token. Until first token arrives, a `warming` event is shown.

### 7.7 Chat panel events (final shape)

| event | when |
|---|---|
| `chat_warming` | while waiting for llama-server to load |
| `embedding_warming` | while waiting for embed server |
| `server_restarting` | `LlamaManager.restart(role)` in flight (chat or embed) |
| `server_ready` | `/health` is back after a restart |
| `server_restart_failed` | new model failed to load; manager fell back to default |
| `phase_changed` | on every phase transition |
| `token_stats` | per LLM call (existing) |
| `provider_status` | updated on every call (new) |
| `loop_repeat` | loop guard fired |
| `stuck` | no-progress detected |
| `context_compacted` | after fold pass |
| `assistant_text` (final / intermediate) | existing |
| `tool_call_start` / `tool_call_result` | existing |
| `coach_message` | agent noticing a manual edit (SP6c) — also used for instruction-drift coaching on `llama_instructions.md` |
| `user_edit_diff` | user save detected by watcher (SP6c) |

---

## 8. SP6c — Real-time file collaboration

### 8.1 Watchdog integration

- New `src/dariusai/agent/file_watcher.py` watches the project dir using `watchdog` (pinned version, trans dep already present or one new entry).
- On every save (write or modify), a `user_edit_diff` event is published with `{path, op: "modified" | "created" | "deleted", diff_summary}` (max 200-char diff).
- Diff summary computed against a snapshot the chat session holds of "what the model last saw".

### 8.2 Chat session reaction

- The chat session subscribes to `user_edit_diff`. When a diff arrives mid-turn:
  - Update the session's snapshot of the file.
  - Inject a system-side note for the next LLM call: "the user edited `<path>` since your last action. Reconcile: did you miss this? Does it contradict what you were about to do?"
  - If the diff is significant (the model was about to call `write_file` on the same path), preempt that tool call — surface the user's edit in the chat panel tagged `coach_blocked_ai_write`.
- The conversation record in the chat log marks the point at which the user edited (`▲ user edited <path>`).

### 8.3 Coaching messages

- When the agent notices a manual edit that contradicts a planned action, it emits a `coach_message` event: a short diagnosis written in the same answer.
- Coach messages are visually tagged `coach:` in the chat panel.
- The agent is **allowed to ask the user a direct question** in a coach message — the next iteration of the chat loop picks up the user's reply as a normal message.

### 8.4 File-tree + AI-edit overlay (UI)

- Front-end (in `src/dariusai/viz/static/index.html`):
  - File tree on the left; clicking a file opens an in-page code editor pane (textarea-based, file content fetched).
  - When the agent calls `write_file`, the editor overlay renders the diff next to the file. The user accepts or rejects.
  - `▲ user edited <path>` indicators appear inline in the chat stream.

The editor overlay is simple — a textarea + diff view — not Monaco. Monaco can be a follow-up. The minimum is text-area-as-editor with line numbers.

---

## 9. Files affected (master list)

```
src/dariusai/agent/
├── chat.py                              # REWRITE — compact-prompt loop, phases, loop/stuck
├── doctrine.py                          # EDIT — system-prompt composition (memory + editing rules appended)
├── graph.py                             # EDIT — LLM factory swap; docstring update
├── llm.py                               # EDIT — BackendChain factory; remove direct AnthropicLLM-construction
├── openai_llm.py                        # EDIT — both online and local-llama-server paths
├── local_llama.py                       # NEW — OpenAI-shaped client pointed at llama-server :7788
├── llama_manager.py                     # NEW — LlamaServer lifecycle (CPU-only pinning)
├── tools.py                             # EDIT — reorder; rewrite descriptions; add okf_* + memory_* tools
├── backend_chain.py                     # NEW — BackendChain (chat) + BackendChain (embed)
├── embedder_manager.py                  # REWRITE — wraps BackendChain (embed)
├── quota_tracker.py                     # NEW — global + per-session counters
├── file_watcher.py                      # NEW — watchdog integration + diff summary
└── constants.py                         # NEW — defaults (idle thresholds, fold cadence, threads policy)

src/dariusai/brain/
├── store.py                             # EDIT — add OKF table (nodes become OKF nodes; legacy skills still import)
├── okf.py                               # NEW — frontmatter parse/serialize; indexer; ULID minting
├── vector_index.py                      # NEW — sqlite-vec wrapper + numpy fallback
└── learn.py                             # EDIT — sources field; OKF-style write path

src/dariusai/model_catalog.py            # REWRITE — providers table becomes chain spec; chat + embed endpoints

src/dariusai/viz/
├── server.py                            # EDIT — WS bridge for file_watcher; SSE for streaming
└── static/index.html                    # EDIT — phase pills, provider-status badge, streaming render, file tree + editor overlay, coach UI

src/dariusai/
└── __init__.py                          # EDIT — bump 0.76.0a0 → 0.77.0a0 (and later to 0.78.0a0 etc. per release)

pyproject.toml                           # EDIT — deps: watchdog, sqlite-vec (with numpy fallback note)
version_lock.json                        # regenerate after each source bump

NEW tests:
├── tests/test_okf_index.py              # OKF index round-trip; vector search hit; snippet truncation
├── tests/test_backend_chain.py          # chain honours quota; falls through on 429; records last success
├── tests/test_quota_tracker.py          # pre-call gate; post-call update; per-session counter
├── tests/test_llama_manager.py          # spawn/stop/idle; pid-file; CPU-only flags
├── tests/test_chat_compact_prompt.py    # verify no raw history sent; verify summary ok
├── tests/test_chat_loop_guard.py        # loop_repeat / stuck emit; rescue prompt injected
├── tests/test_chat_phases.py            # phase_changed event; transitions render
├── tests/test_chat_streams.py           # tokens stream from first delta
├── tests/test_chat_budget.py            # MAX_TOOL_ITERATIONS scales with quota
├── tests/test_file_watcher.py           # edit diff produced; injected into chat session
└── tests/test_coach_messages.py         # coach_message emitted; blocked write when user edited
```

Total: ~5 new files in `src/dariusai/agent/`, ~2 new in `src/dariusai/brain/`, ~11 new tests, edits across ~10 existing files. Per the project rule: each merged scope bumps the version and relocks.

---

## 10. Dependencies

### 10.1 PyPI additions (with reason)

- `watchdog` — the only credible cross-platform file watcher. `pip install watchdog` is the de facto standard. Already used by some sub-deps; verify on first CI run.
- `sqlite-vec` — sqlite extension; pure-Python wheel; no separate process. **With fallback.** If unavailable in the bundled SQLite at runtime, fall back to `numpy + cosine` over `.npy`.
- (test-only) `pytest-asyncio` — only if SP6b introduces async streaming paths. May not be needed.

### 10.2 Pinned external assets

- `llama.cpp` GitHub release — binary sha-256 verified at design finalisation; pinned at a stable commit (e.g., `b4500` or current). One-time download.
- `Qwen2.5-Coder-7B-Instruct-GGUF Q4_K_M` — chat model default. One-time download.
- `nomic-embed-text-v1.5-GGUF Q4_K_M` — embed model default. One-time download.

Pin lists (file list, sha256, mirror order) live in `src/dariusai/agent/llama_manager.py` constants. Settings allows override.

### 10.3 No new online services

The chat runs on the user's chosen providers (already integrated). The OKF indexer is local. New HTTP routes only to existing providers.

---

## 11. Data / API changes

### 11.1 Disk layout additions

- `<project>/.dariusai/memory/` — new dir; gitignored (similar to `.venv/`).
- `%LOCALAPPDATA%\DariusAI\models\` — chat + embed models, gitignored.
- `%LOCALAPPDATA%\DariusAI\bin\` — `llama-server.exe`, gitignored.

### 11.2 SQLite schema additions

```
nodes gets:
  type TEXT
  anchors JSON
  source TEXT
  embedding_ref INTEGER

new table okf_meta:
  id TEXT PRIMARY KEY
  type TEXT
  title TEXT
  tags JSON
  created_at REAL
  updated_at REAL
```

BrainStore's existing `nodes` table remains the row store for legacy skills (those imported by `omni_import`). OKF nodes can live alongside in `okf_meta` (cleaner) or in the same table with `type` populated.

Decision: **single `nodes` table** with `type` column populated for OKF nodes; legacy skill rows keep `type='skill'`. Simpler migration, fewer tables.

### 11.3 Public API changes

- `ChatSession.send(text, on_event=None)` — unchanged signature; behaviour changed (compact prompt). Backward compatible.
- `build_llm(store, model=None)` — unchanged signature; returns one of `AnthropicLLM`, `OpenAILLM`, `LocalLlamaLLM` chosen by the active backend chain.
- New: `build_backend_chain(store, kind)` — public factory for tests.
- `BrainStore.search()` — deprecated; OKF replaces it. New: `BrainStore.okf_search(query, type=None)`.
- `BrainStore.learn()` becomes `BrainStore.add_okf_node(body, type, ...)`.

### 11.4 Settings page

- New "Provider chain" section. Drag-and-drop ordering, per-tier API key, model picker for chat, model picker for embed, monthly cap fields.
- New "Model library" section. Folder list (add/remove/rescan), model table (name, kind, size, context, template marker, thinking badge), per-model role slot.
- New "Embedding" section. Backend indicator, model path, server status, redownload button.
- New "Runtime" section. Thread policy per server, idle shutdown minutes per server, server status (PID + uptime + last health check), three reset buttons (chat / embed / all), thinking toggle when the active chat model supports reasoning.
- New "Local llama instructions" card. Multi-line `<textarea>` bound to `<project>/.dariusai/llama_instructions.md` (file-backed; OKF-indexed). Buttons: "Reset to default" (copies the shipped file in `addon/skills/agent-orchestration/local-llama-instructions.md`), "Open in editor" (opens in the SP6c editor or OS default), "Reload from disk". Live readout: word count + token estimate + first-line preview. Active state pill: "local-llama instructions in use" when the chat slot is on local GGUF.

---

## 12. Security

### 12.1 No new attack surface

- New disk assets are gitignored (no committed model files or binaries).
- New outbound routes are the providers already integrated (HTTP `POST`, HTTPS by default).
- New inbound route: `viz/server.py` exposes a local-only `127.0.0.1` SSE stream. No new public port.

### 12.2 llama.cpp binary integrity

- Verify the SHA-256 against the GitHub release manifest before saving the binary.
- Refuse to spawn if the hash doesn't match. Surface a clear error.

### 12.3 OKF body content

- Indexed MD bodies stay inside the project dir. The vector store stays on disk. No outbound vector service.
- The brain never returns a body via `okf_search` — only snippets — preventing a single call from dumping PII from a large MD into context.

### 12.4 User-edited MD files

- File watcher does NOT execute file contents. It only diffs and emits an event. Manual-edit coaching is read-only.
- `Live_version.md` writes require an explicit `memory_set_live(body)` call — no implicit overwrite.

### 12.5 Rate limit / DoS

- QuotaTracker ensures the harness never issues more than `monthly_call_cap` calls per provider in a month.
- Backstop: per-minute safety floor on the chain (`MAX_CALLS_PER_MIN=120` default).

---

## 13. Testing strategy

### 13.1 Per-component unit tests

- `test_okf_index` — frontmatter round-trip; ULID minting and ordering; vector search returns hits with decreasing scores; snippet is truncated to 200 tokens.
- `test_backend_chain` — chain honours quota; falls through on 4xx; records last success; warm-path skips probes after recent success; raises AllBackendsFailed when all exhausted.
- `test_quota_tracker` — pre-call gate; post-call update from headers; per-session counter; 429 marks exhausted until.
- `test_llama_manager` — spawn/stop/idle; pid-file is clean; `--threads` flag applied correctly; CPU-only flags (no GPU flags).
- `test_chat_compact_prompt` — verify no raw history sent; verify summary included; verify Live_version included; verify OKF top-K injected.
- `test_chat_loop_guard` — `loop_repeat` after 3 identical calls; `stuck` after no-progress; rescue prompt appears in next system message.
- `test_chat_phases` — `phase_changed` event fired; transitions render.
- `test_chat_streams` — first delta within 100ms of `complete_stream` call.
- `test_chat_budget` — `MAX_TOOL_ITERATIONS` scales down as quota shrinks.

**Model library + llama-server tests (NEW):**

- `test_model_library` — `add_folder` returns sorted `ModelEntry`s; `list_models(kind="chat"|"embed"|None)` filters correctly; `set_role` updates the active slot and triggers an auto-restart when the running model differs; sha256 cache makes a re-scan free (no re-parse).
- `test_gguf_introspection` — given a small fixture GGUF, extract `context_length`, `chat_template`, `architecture`, and `sha256`; missing fields fall back to safe defaults (`-c 8192`, default jinja, `kind=unknown`).
- `test_can_think_detection` — chat template containing `{%- if think %}<think>...</think>` → True; filename markers (`qwen3`, `qwq`, `*-think*`, `deepseek-r1`) → True; default = False.
- `test_server_restart` — `LlamaManager.restart("chat")` kills the existing process, removes the pid-file, spawns with the new model; the chat panel's `server_restarting` → `server_ready` event sequence fires; `restart` while a turn is in-flight surfaces `server_restart_failed` instead of corrupting the loop.
- `test_thinking_toggle` — toggle ON persists the chat-template override across restarts; toggle OFF strips `<think>` blocks without rewriting the rest of the template.
- `test_llama_instructions_file` — file round-trip; default content matches the shipped file at `addon/skills/agent-orchestration/local-llama-instructions.md`; OKF index treats the file as `agent_note`.
- `test_local_llama_instructions_resolver` — `system_prompt_for(backend)` returns the file body when `backend.kind == "local"` and the file exists; returns doctrine otherwise.
- `test_local_llama_instructions_settings` — Settings "Reset to default" button copies the shipped file verbatim; "Reload from disk" reflects external edits (e.g. SP6c-triggered).

### 13.2 Integration tests

- `test_coach_messages` — user edit mid-turn; chat session reacts; `coach_message` event fires; subsequent `write_file` is preempted.
- `test_provider_chain_round_trip` — wire a synthetic transport that emits 200 on the second tier; chain skips tier 1; result comes from tier 2.
- `test_fold_pass` — run 8 turns; verify a `FullContext.md` entry was appended; verify `Live_version.md` was either updated or left alone based on user override.

### 13.3 Regression tests for the existing test suite

All 16 existing test files under `tests/test_chat_*` and `tests/test_brain_*` must keep passing. The chat rewrite in SP6b touches the largest chunk of pre-existing tests. Adapter is needed for any test that hand-rolled an `LLM` stub — those still pass because the LLM Protocol is unchanged.

### 13.4 Live verification (out-of-band CI)

- Run the harness on a fresh Windows box. First-run download: llama-server (~30 MB), chat model (~4.5 GB), embed model (~80 MB). Spawn both. Probe `/health`. Run a basic chat turn. Verify streaming. Verify OKF vertex search.
- Run with provider offline (network drop). Verify chain falls through to local GGUF.
- Run with provider rate-limited (mock 429). Verify pre-call gate prevents the call.
- Run with `monthly_call_cap=4`. Verify the chat loop shortens near the ceiling.

---

## 14. Verification strategy

Per `AGENTS.md §27`, every step verified by independent signal:

- **BackendChain tier skip** → unit test + synthetic transport + assert which tier the response came from.
- **OKF vertex search** → sqlite-vec query + manual snippet match against an indexed body.
- **llama.cpp spawn** → `process.poll()` returns None + `/health` returns 200 + first LLM call returns non-empty.
- **Compact prompt** → capture the outbound payload in a test transport; assert it does NOT contain the full history; assert it DOES contain the summary + Live_version + OKF hits.
- **Loop guard fires** → mocked LLM emits the same call 5 times; assert `loop_repeat` event + rescue prompt injected.
- **File-watcher reacts** → user_edit_diff event; chat session reacts with coach_message; pre-empted write.
- **Final:** fresh-clone simulation — wipe `%LOCALAPPDATA%\DariusAI\` and `<project>/.dariusai/memory/`; confirm first-run UX warms servers, builds index, runs a turn end-to-end.

---

## 15. Rollback

- SP6a (OKF + backend chain) — additive. Removing it disables vertex search + chain fallback; harness reverts to single-provider + keyword search. Clean revert.
- SP6a-embed (llama.cpp) — additive. Disabling the runtime reverts to online-only.
- SP6b (compact prompt loop) — touches the chat loop substantially. Reverting requires restoring `chat.py` from git and re-running the existing tests. Doc-level revert.
- SP6c (file watcher) — additive. Disabling turns off real-time coaching.

If SP6 needs to be entirely reverted:

```
git revert <each SP6 commit>
python tools/bump_version.py --set 0.76.0a0
```

Model files cached in `%LOCALAPPDATA%` are out of the repo and not affected.

---

## 16. Risks

| risk | severity | mitigation |
|---|---|---|
| Provider rate limits hit before the chain switches (provider A 429s, no probe) | medium | Pre-call gate consumes the QuotaTracker; chain moves on without trying |
| `sqlite-vec` missing in bundled SQLite | medium | Verified at startup; numpy fallback; chat panel reports degraded mode |
| llama-server binary tampering on first download | low | SHA-256 verification against release manifest; refuse if mismatch |
| Local GGUF chat model slow on a 4-core laptop | medium | Streaming-first UX; per-tier `MAX_TOOL_ITERATIONS` scales with quota and clock |
| Open Knowledge Format terminology collides with OKFN | low | Doc explicitly defines OKF as this project's node schema, names Open Knowledge Foundation where it ever needs to be referenced |
| Compact prompt drops a detail the model needed | medium | Fold pass writes to `FullContext.md` (recoverable); the agent can `okf_read` any past id; OKF vertex search catches related nodes |
| User-edits racing with agent writes | low | Pre-emption (8.2) detects and reconciles; user always wins |
| 4 concurrent sessions × 4 backend tiers each = 16 simultaneous providers may exhaust TCP/IP sockets or DNS | low | QuotaTracker pre-call gate already keeps the request count low; sockets rarely run out before quotas |
| AGENTS.md §7 references BrainFIX.md but not this file | medium | New `AGENT_CAPABILITY_FIX.md` at root mirrors BrainFIX.md style; AGENTS.md §7 updated to pick both up |
| Version lock churn on many source-file edits | low | One `--minor` per merged scope; lock fingerprint regenerated each time |
| Model library hits a folder with many large files (10k+) | medium | First scan is async; progress emitted; subsequent rescans are hash-cached; user can pause/cancel via the Settings card |
| GGUF metadata missing `*.general.context_length` | medium | Runtime falls back to `-c 8192`; Settings exposes per-model `-c` override |
| Two models share the same filename in different folders | low | `id = ULID` (always unique); the table shows the resolved path so collision is visible |
| Model picker user pastes an `agents.md` that overlong doctrine replaces | low | The textarea ships a token estimate; Settings shows a warning if the override > 2k tokens (the threshold at which Q4_K_M models degrade) |
| `library_set_role` triggers an auto-restart mid-turn and corrupts a tool call in-flight | medium | Restart only happens when a NEW model is needed; in-flight turns complete on the current model first; restarts queue, never preempt |
| OKF sees `<project>/.dariusai/llama_instructions.md` as `agent_note` and a vertex search returns it as a hit, leaking the override into context unexpectedly | low | The OKF index excludes paths under `.dariusai/memory` AND `.dariusai/llama_instructions.md` by default; explicit opt-in via Settings |

---

## 17. Decisions log

- **Two llama-server processes.** Each is single-purpose (chat or embed) so each can be tuned independently. **Not** one process serving both — they'd need to share a model.
- **CPU-only.** Locked; no GPU flag in Settings; not deferred.
- **Local GGUF primary, providers second.** Online is a *boost*, not a requirement.
- **minimax.io + agnes-ai + NVIDIA free + local GGUF** are the defaults; order is user-configurable.
- **Compact-prompt per turn.** No raw history sent; full history on disk as MD; summary reconstructed by fold pass.
- **OKF as the new memory schema.** MD files + frontmatter + vector index. BrainStore's `nodes` table extended with `type` rather than a new table — fewer migrations.
- **`sqlite-vec` preferred; numpy fallback.** No new external services.
- **Phases are self-reported.** The chat loop doesn't enforce them; pill rendering only. Prevents the loop from breaking itself off-ramp.
- **Loop guard never aborts.** Emits events + injects rescue prompts; user retains "Continue" control.
- **Manual-edit coaching pre-empts agent writes** (8.2). User's edit wins; the agent reconciles.
- **Live_version.md ownership = user.** Agent may *suggest* updates via coach messages; never overwrites silently.
- **No multi-agent orchestration (SP6d) in this round.** Independent chat sessions in parallel, yes; sub-agents in one task, no.
- **No editor UX deep-dive (SP6e) in this round.** Plain textarea + diff view is the minimum; Monaco is a follow-up.
- **ULID for ids.** Sortable, timestamp-encoded, supports `grep <ulid> FullContext.md` jump-to.
- **Two-anchor format:** every `ChangeLog-<ulid>.md` is referenced inline in `FullContext.md` at the same ULID.
- **Model files are user-chosen.** The harness ships default pins for offline-first (still works out-of-the-box), but the model library is the user's territory — multiple folders, swap anytime, no automatic switching.
- **GGUF introspection uses `gguf-py`** (small PyPI dep). Same source the `llama.cpp` ecosystem uses; cached on sha256 so re-scanning a folder is free.
- **`can_think` heuristic.** Detection combines chat-template signature + filename markers + architecture flag. Detection is automatic; activation is manual (toggle default OFF).
- **Per-role slot with auto-restart.** Setting a model to a role that differs from the running one triggers `LlamaManager.restart(role)`. In-flight turns finish on the existing model; the restart queues.
- **Reset server is an explicit user action** via the Settings card. The button exists because swapping model mid-session is otherwise a manual kill.
- **`llama_instructions.md` is the user-tuned local override.** Stored as a file (not a Settings blob) so it shows up in the agent's OKF index as `agent_note` and the SP6c file watcher can coach on instruction drift. Replaces the doctrine only when the active chat backend is local GGUF; online providers keep the doctrine.
- **Default `llama_instructions.md` content ships at `addon/skills/agent-orchestration/local-llama-instructions.md`** — a streamlined llama-friendly version of the doctrine, sized for what a Q4_K_M 7B model actually follows. The Settings "Reset to default" button copies the shipped file verbatim into the project-local path.
- **No OKF read-only paths can leak by default.** `llama_instructions.md` and other agent-private files are excluded from vertex search unless the user opts in — the OKF index treats paths starting with `.dariusai/` as private (with one explicit exception for `.dariusai/memory/`).

---

## 18. Open / TBD

These are points the design does not yet nail down. Each becomes a task in `docs/superpowers/plans/2026-08-15-agent-capability-fix.md` (written via writing-plans once this design is approved):

- **`browse_okf`** — do we add a structural browser like `browse_brain`? Yes, in SP6a — but the exact API shape (anchor-keyed vs id-keyed) is a small decision to confirm at plan time.
- **Provider key migrations** — the existing Settings/providers table gets re-shaped into the chain. Whether to do an in-place migration or build fresh is a plan-time decision.
- **`DARIUSAI_*` env-var names** — new env vars for idle thresholds, threads policy. Names pending finalisation.
- **Top-K for OKF hits in the prompt.** K=4 default; could vary by phase. Plan will pick one.
- **Settings drag-and-drop for chain order.** UI heavy or accept a simpler list-with-arrows? Plan picks the lighter.
- **Front-end live edit overlay.** Accept a textarea + diff view (lighter) or pull Monaco (heavier)?
- **Bundle size.** Spinning up llama-server is heavier than just calling providers. On RAM-constrained machines (8 GB), running the chat model alongside the IDE may push memory. Plan monitors.

---

## 19. Companion dossier

`AGENT_CAPABILITY_FIX.md` (project root, mirrors `BrainFIX.md`) carries:

- Same DECISIONS table (so AGENTS.md §7 finds it)
- DAILY LOG (so the changelog is at the agent-workspace level)
- Status per sub-project (SP6a-embed, SP6a, SP6b, SP6c)
- Blocked / TODO items

The dossier is the index; this spec is the depth. They cannot disagree; if the spec updates, the dossier updates.

---

## 20. Validation (what counts as "done")

This spec is done when:

- All fifteen design checks (sections 1–19) are non-empty and consistent.
- Open items (section 18) are listed with owners and target SPs.
- The user has reviewed and approved the entire spec.

The spec is **not** done when:

- Decisions are still in flight between the user and the spec (current state).
- Phases are decided but the spec is silent on what "done" means for each.

After approval:

- Spec → `docs/superpowers/specs/2026-08-15-agent-capability-fix-design.md` (this file, frozen).
- Dossier → `AGENT_CAPABILITY_FIX.md` at root (initialised with status = DESIGN-APPROVED).
- AGENTS.md §7 updated to reference the dossier.
- Next: `writing-plans` skill to produce `docs/superpowers/plans/2026-08-15-agent-capability-fix.md`.
