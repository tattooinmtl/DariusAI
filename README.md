# DariusAI Harness

A self-learning polyglot coding agent with a persistent, queryable skill graph,
wrapped in a desktop app: a live neural view of what the agent knows and is
doing, a code editor, a chat pane, and a project workbench.

Windows desktop application. Python 3.11+.

## What is in here

- **Agent loop** — Planner → Coder → Tester → Verifier → Memory Writer.
- **Brain** — a SQLite + NetworkX skill graph with markdown skill bodies, so
  what the agent learns persists between sessions and can be searched.
- **Skill library** — 65 skills under `addon/skills/`, imported into the brain
  on install.
- **Sandbox** — path confinement, Windows job objects for real process-tree
  kill, and credentials scrubbed from child environments.
- **Workbench** — 17 project templates, scaffolded and set up with their
  dependencies installed.

## Install

```powershell
./install.ps1
```

Clones the repository, creates a virtual environment, installs the package,
imports the skill library into the brain, and creates the Desktop and Start
Menu shortcuts.

Options: `-Dest <path>`, `-Branch <name>`, `-Repo <url>`, `-SkipShortcuts`.

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

## Command line

| Command | What it does |
|---|---|
| `dariusai viz` | Open the desktop window |
| `dariusai serve` | Run the server without a native window |
| `dariusai run <task>` | Run the agent loop on one task |
| `dariusai chat` | Interactive terminal REPL |
| `dariusai import-addon` | Import `addon/skills` and `addon/hooks` into the brain |
| `dariusai import-skills` | Import an external skill library |
| `dariusai install-shortcuts` | Create the Desktop / Start Menu shortcuts |
| `dariusai --version` | Print the version |

`run` and `chat` need a model provider. Set `ANTHROPIC_API_KEY`, or configure a
provider in the app's Settings — keys saved there are encrypted with Windows
DPAPI and stored in the brain directory (`~/.dariusai`), never in this
repository.

## Development

```powershell
.venv\Scripts\python -m pytest
```

The version is fingerprinted: changing anything under `src/` or `launch.pyw`
without bumping the version fails `tests/test_version_lock.py`. Bump with
`tools/bump_version.py`.
