# DariusAI Harness

A self-learning polyglot coding agent with a persistent, queryable skill graph,
wrapped in a desktop app: a live neural view of what the agent knows and is
doing, a code editor, a chat pane, an integrated terminal, a project workbench,
and a Blender bridge for real 3D asset creation.

Windows desktop application. Python 3.11+.

## Install

One line from PowerShell:

```powershell
irm https://raw.githubusercontent.com/tattooinmtl/DariusAI/main/install.ps1 | iex
```

That clones the repo, creates a virtual environment, installs the package,
imports the skill library into the brain, and creates the Desktop and Start
Menu shortcuts. Safe to re-run — an existing checkout is updated, never
clobbered.

Options for the script directly:

```powershell
./install.ps1                       # into $HOME\dariusai-harness
./install.ps1 -Dest D:\apps\darius  # somewhere else
./install.ps1 -Branch dev           # a non-main branch
./install.ps1 -SkipShortcuts        # no Desktop / Start Menu icons
```

Manual equivalent, from a checkout:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\dariusai import-addon
.venv\Scripts\dariusai install-shortcuts
```

## Run

Launch from the Desktop shortcut, or:

```powershell
.venv\Scripts\pythonw launch.pyw
```

## What's in the app

### The desktop window
A dockable workspace with nine slots. Every panel can float, dock, close and
reopen, and remembers its position between sessions.

- **Neural view (3D brain)** — every skill, tool and hook in the brain drawn
  as a node. Nodes pulse when the agent uses them; the coordinator node
  thinks while a chat turn runs. Click a node to open its content.
- **Chat panel** — long-lived conversation with the agent. Tool calls stream
  live under a collapsible "thinking" box; a token gauge shows how much of
  the model's context window is in play. Auto-compaction keeps long chats
  under the ceiling.
- **Editor** — CodeMirror with syntax highlighting, file tree, right-click
  menu for delete/rename/move/copy/mkdir/new file, graded deletion
  protection (the project root is refused with no override).
- **Integrated terminal** — full shell, respects the project directory.
- **Workbench** — 17 project templates. New projects are scaffolded with
  their dependencies installed and their language toolchain ready.
- **Settings** — providers and API keys (DPAPI-encrypted), preferences,
  start-with-Windows, layout reset.
- **Blender bridge** — status light in the title bar (green / orange / red)
  showing whether Blender's MCP bridge is reachable.

### The agent

- **Chat REPL** — `dariusai chat` for the terminal version of the same agent.
- **Single-task runs** — `dariusai run <task>` for the Planner → Coder →
  Tester → Verifier → Memory Writer pipeline.
- **Provider-agnostic** — Anthropic (Claude), OpenAI, MiniMax M-series, and
  any OpenAI-compatible provider. Configured per-project in Settings.
- **Tool cap** — 60 tool calls per turn by default; overridable per session.
- **Auto-compaction** — history collapses at 75% of the context window so
  long turns don't hit the ceiling.
- **Prompt caching** — cache-read/cache-write tokens tracked and displayed.

### The brain

- **SQLite + NetworkX skill graph** — every skill the agent has, every tool
  it can call, every hook it's learned, in one graph with markdown bodies.
- **196 skills** across 30+ categories (`addon/skills/`), imported into the
  brain on install. Categories: agent-orchestration, apple, autonomous-ai-
  agents, creative, data-science, design, devops, diagramming, email,
  gamedev, gaming, github, languages, mcp, media, mlops, productivity,
  red-teaming, research, and more.
- **Passage-level RAG** — a single FTS5-backed index over every SKILL.md
  in the tree, so `skill_lookup` returns the paragraphs matching a query
  rather than loading whole files.
- **Skill payload eviction** — a skill body that's been read and acted on
  is replaced with a one-line receipt after two more iterations, so a
  60-iteration turn doesn't re-send the same 26 KB every step.

### The sandbox

Every filesystem and shell tool goes through a rooted sandbox:

- **Path confinement** — resolves before checking, so `..`, symlinks and
  absolute-path traversals can't escape the project root.
- **Windows job objects** — timeouts kill the whole process tree, not just
  the shell that was launched.
- **Credential scrubbing** — API keys stripped from every subprocess env.
- **External-read grants** (v0.83+) — the agent can request one-turn
  read-only access to a directory outside the sandbox (e.g. to study a
  reference project). A modal asks the user; on approval the folder and
  its subtree become readable, writes are still refused, and destructive
  shell commands (rm/del/mv/git reset/git clean) are blocked in the
  granted tree. The grant clears at the next user turn.

### The Blender bridge

Drive Blender from DariusAI over MCP to produce real 3D assets — parametric
structures, sculpted organic shapes, hard-surface props — and export them
in formats every engine imports. No paid services required.

**Parametric block-out** — `game3d_build_structure` (house, tower, wall,
gate, storage, shrine, workshop), `game3d_build_unit` (worker, melee,
ranged, mounted, caster), `game3d_apply_palette` (stone, timber, desert,
verdant, obsidian, frost, crimson, azure).

**Procedural modeling** — `game3d_extrude`, `game3d_bevel`, `game3d_boolean`
(UNION/DIFFERENCE/INTERSECT), `game3d_subdivide`, `game3d_apply_texture`,
`game3d_load_reference` (image plane on any of six axes for modeling from
concept art), `game3d_join`.

**Sculpting** — not brush-driven, since an LLM can't drag a stylus.
Instead, every sculpt op takes a **region spec** (a sphere, a box, a
half-space) and applies a brush-equivalent operation to just the vertices
that region selects. `game3d_sculpt_start`, `_displace`, `_smooth`,
`_pinch`, `_grab`, `_from_metaballs` (spheres/ellipsoids/capsules blended
by Blender's native metaball evaluator), `_from_sdf` (whitelisted SDF
expression evaluated on a grid, marched into a mesh).

**Export** — `game3d_export_model` writes `.glb` / `.gltf` / `.fbx` /
`.obj` / `.stl`. Unity, Unreal, Godot, Three.js and Babylon.js all import
`.glb` natively.

Blender's MCP bridge listens on `127.0.0.1:8765`. DariusAI's own web UI
uses `8780` so the two never fight over the port. Install the add-on from
Settings → Blender → Install (headless install; no zip, no clone).

## Command line

| Command | What it does |
|---|---|
| `dariusai viz` | Open the desktop window |
| `dariusai serve` | Run the server without a native window |
| `dariusai run <task>` | Run the agent loop on one task |
| `dariusai chat` | Interactive terminal REPL |
| `dariusai import-addon` | Import `addon/skills` and `addon/hooks` into the brain |
| `dariusai import-skills` | Import an external skill library |
| `dariusai import-external` | Import from the `external_skills/` folder |
| `dariusai install-shortcuts` | Create the Desktop / Start Menu shortcuts |
| `dariusai --version` | Print the version |

## Model providers

`run` and `chat` need a model provider. Two ways to configure:

1. **In the app** (recommended) — Settings → Providers. Keys are
   DPAPI-encrypted and stored in the brain directory (`~/.dariusai`),
   never in this repository.
2. **Environment variables** — copy `.env.example` to `.env` and fill in
   `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`. `.env` is gitignored;
   the example is committed as documentation.

Supported: Anthropic Claude (all models, including 4.7 / 4.6), OpenAI
(GPT-4o and 5.x when released), MiniMax M-series, and any OpenAI-
compatible endpoint. Provider presets ship for the common ones; adding a
custom endpoint is a form fill.

## Development

```powershell
.venv\Scripts\python -m pytest
```

The version is fingerprinted: changing anything under `src/` or
`launch.pyw` without bumping the version fails `tests/test_version_lock.py`.
Bump with `tools/bump_version.py`.

Full test suite covers the sandbox (confinement, process-tree kill,
credential scrubbing, external grants), the tool registry, the chat
session, auto-compaction, skill eviction, the Blender bridge
(handshake, archetypes, materials, background render), the sculpting
maths (region predicates, SDF primitives, expression evaluator,
marching cubes), and the viz backend.

## Requirements

- **Windows 10/11**. The DPAPI encryption for API keys is Windows-
  specific, as is the Windows job-object sandbox and the shortcut
  installer.
- **Python 3.11+**. 3.12 works; 3.10 is not tested.
- **git** on PATH.
- **Blender 5.2+** for 3D tooling (optional — the app runs without it,
  but the `game3d_*` tools require the MCP bridge).

## License

MIT. See `LICENSE`.
