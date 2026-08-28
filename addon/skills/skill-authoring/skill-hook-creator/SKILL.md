---
name: skill-hook-creator
description: >-
  Scaffold, construct, validate, register, and dispatch new skills and lifecycle
  hooks across AI agents and harnesses — Omni, Claude Code, Codex, Cursor, Pi,
  Hermes, Factory, OpenCode, Devin, NimAgent, Gemini CLI, Kimi Code CLI.
  Use when creating new skills, adding hooks, installing hooks into a target
  agent's home directory, or generating the per-agent AGENTS.md /
  CLAUDE.md / GEMINI.md / KIMI.md / PI.md entry points that drive the
  plan-and-dispatch workflow.
---

# Skill & Lifecycle Hook Creator Guide

This skill ships a small Node.js toolbox (`tools/*.js`) and a set of canonical
**agent entry-point files** (`examples/AGENTS.md`, `CLAUDE.md`, `GEMINI.md`,
`KIMI.md`, `PI.md`, `OMNI.md`) that together let you:

1. Build a hook (JSON / TOML / YAML config) — `tools/scaffold-hook.js`.
2. Lint the hook for required fields, secret leaks, unsafe shell patterns — `tools/validate-hook.js`.
3. Test the hook in isolation — `tools/test-hook.js`.
4. Install it into one or more agents' home directories — `tools/install-hook.js`.
5. Audit every registered hook across every harness — `tools/audit-hooks.js`.
6. Export the registry to a portable bundle — `tools/export-hooks.js`.
7. Drop a per-agent `AGENTS.md` so the agent knows which skills belong to
   which phase of your plan.

## 1. Automated CLI Tool Usage

All tools are Node.js. From the skill root:

```bash
# Scaffold a new hook
node tools/scaffold-hook.js --name security-guard \
  --event PreToolUse --matcher run_shell \
  --command "node hooks/harness_guard.js" --timeout 30

# Validate it (required fields, secrets, unsafe shell patterns)
node tools/validate-hook.js hooks/security-guard.json

# Test it with a sample payload
node tools/test-hook.js hooks/security-guard.json --payload '{"tool":"run_shell","args":{"command":"rm -rf /"}}'

# Install it into one or more agents
node tools/install-hook.js hooks/security-guard.json --harness omni
node tools/install-hook.js hooks/security-guard.json --harness claude
node tools/install-hook.js hooks/security-guard.json --harness gemini
node tools/install-hook.js hooks/security-guard.json --harness kimicode --dry-run

# List / audit / export everything that's registered
node tools/list-hooks.js
node tools/audit-hooks.js --harness omni
node tools/export-hooks.js --out bundle.tar

# Remove or update later
node tools/remove-hook.js hooks/security-guard.json --harness omni
node tools/update-hooks.js --harness claude

# Drop the per-agent entry-point file (AGENTS.md / CLAUDE.md / GEMINI.md / …)
node tools/install-agents-md.js --harness omni
node tools/install-agents-md.js --harness all
```

## 2. Skill & Tool Layout

```
skill-hook-creator/
├── SKILL.md                 ← this file
├── tools/                   ← Node.js CLI toolbox (all the commands above)
│   ├── scaffold-hook.js
│   ├── validate-hook.js
│   ├── test-hook.js
│   ├── install-hook.js
│   ├── install-agents-md.js ← NEW: drop the per-agent entry-point file
│   ├── list-hooks.js
│   ├── audit-hooks.js
│   ├── export-hooks.js
│   ├── remove-hook.js
│   └── update-hooks.js
├── hooks/                   ← your generated hook configs go here
└── examples/
    ├── AGENTS.md            ← canonical plan-and-dispatch template
    ├── CLAUDE.md            ← Claude Code variant (same template, Claude-specific notes)
    ├── GEMINI.md            ← Gemini CLI variant
    ├── KIMI.md              ← Kimi Code CLI variant
    ├── PI.md                ← Pi CLI variant
    ├── OMNI.md              ← Omni Agent variant
    └── hooks/               ← reference hook configs (security-guard, lint-on-save, etc.)
```

## 3. Hook Contract

Lifecycle hooks register under one of five events:

| Event           | Purpose                                                          |
|-----------------|------------------------------------------------------------------|
| `PreToolUse`    | Block / gate / modify arguments before tool execution.            |
| `PostToolUse`   | Post-execution analysis, formatting, auto-cleanup.               |
| `PreInvocation` | Inject context or planning steps before LLM generation.          |
| `PostInvocation`| Evaluate response; force-continue or terminate.                  |
| `Stop`          | Evaluate loop completion; re-enter loop if goals remain unmet.   |

A hook file (JSON / TOML / YAML) must have:

```json
{
  "name":     "security-guard",
  "trigger":  "PreToolUse",
  "matcher":  "run_shell",
  "command":  "node hooks/harness_guard.js",
  "timeout":  30,
  "env":      { "RULES": "strict" }
}
```

`validate-hook.js` enforces:

- `name` and `command` are non-empty strings.
- `trigger` is a known event (or warns).
- No hardcoded API keys / tokens / passwords.
- No unsafe shell patterns (`rm -rf /`, `| sh`, `sudo rm`, `git push --force`, …).
- Schema-driven required fields if you pass `--schema`.

## 4. Supported Harnesses

| Key         | Name           | Hooks dir                  | Config file                    | Hook field     |
|-------------|----------------|----------------------------|--------------------------------|----------------|
| `omni`      | Omni Agent     | `~/.omni/hooks`            | `~/.omni/omni.config.json`     | `hooks`        |
| `claude`    | Claude Code    | `~/.claude/hooks`          | `~/.claude/settings.json`      | `hooks`        |
| `codex`     | OpenAI Codex   | `~/.codex/hooks`           | `~/.codex/hooks.json`          | —              |
| `cursor`    | Cursor         | `~/.cursor/hooks`          | `~/.cursor/settings.json`      | `hooks`        |
| `pi`        | Pi Dev         | `~/.pi/hooks`              | —                              | —              |
| `hermes`    | Hermes Agent   | `~/.hermes/plugins/command-guard` | `~/.hermes/config.json` | `commandGuard` |
| `factory`   | Factory AI     | `~/.factory/hooks`         | `~/.factory/settings.json`     | `hooks`        |
| `opencode`  | OpenCode       | `~/.config/opencode/plugins` | `~/.config/opencode/config.json` | `plugins`   |
| `devin`     | Devin          | `~/.devin/hooks`           | `~/.devin/config.json`         | `hooks`        |
| `nimagent`  | NimAgent       | `~/.nimagent/hooks`        | `~/.nimagent/config.json`      | `hooks`        |
| `gemini`    | Gemini CLI     | `~/.gemini/hooks`          | `~/.gemini/settings.json`      | `hooks`        |
| `kimicode`  | Kimi Code CLI  | `~/.kimi-code/hooks`       | `~/.kimi-code/config.toml`     | `hooks`        |

Pass `--harness all` to install across every supported agent.

## 5. Plan-and-Dispatch Workflow (AGENTS.md)

Each supported agent looks for an entry-point file at the root of the project
(or its home directory) named after the agent — `AGENTS.md` is the canonical
fallback. The file describes a plan broken into phases, with skills attributed
to each phase. When the agent starts a session, it reads the file and knows
which skill to invoke per phase.

### 5.1 The phases

1. **Understand** — restate the goal, ask one focused clarifying question if
   the request is ambiguous, otherwise state your interpretation.
2. **Explore** — invoke `using-addon-skills` to map the stack, then dispatch
   to domain-specific skills (`/codebase-inspection`, `/codebase-starters`,
   `/python`, `/web`, etc.) to gather context.
3. **Plan** — use `/plan` (or `writing-plans`) to write phases to
   `omni/todos.json` via `project_todo`, deciding what "done" means for each
   phase (which tests / build / lint must pass).
4. **Implement** — apply changes in small increments via `apply_patch` /
   `edit_file` / `write_file`. Match surrounding code conventions.
5. **Verify** — run the relevant `run_test`, `run_shell`, `test_coverage`,
   `security_scan`, then `git_diff` to confirm exactly what changed.
6. **Report** — mark todos done, summarize concisely: what changed, how it
   was verified, anything left open.

### 5.2 Skill → phase attribution

| Phase        | Primary skills (slash commands)                                                          |
|--------------|------------------------------------------------------------------------------------------|
| Understand   | `/brainstorming`, `/prompt-me`                                                           |
| Explore      | `/using-addon-skills`, `/codebase-inspection`, `/rag_search`, `/find_symbol`, `/lsp`     |
| Plan         | `/plan`, `/writing-plans`, `/before-building`, `/next-decision`                          |
| Implement    | `/dispatching-parallel-agents`, `/subagent-driven-development`, `/git-worktrees`         |
| Verify       | `/test-driven-development`, `/systematic-debugging`, `/verification-before-completion`  |
| Report       | `/requesting-code-review`, `/handoff`                                                    |

### 5.3 Per-agent variants

`install-agents-md.js --harness <name>` copies `examples/<NAME>.md` to the
target agent's home directory and prints the path. Use `--harness all` to fan
out everywhere.

| Agent   | Source             | Destination                                |
|---------|--------------------|--------------------------------------------|
| Omni    | `examples/OMNI.md` | `~/.omni/AGENTS.md`                        |
| Claude  | `examples/CLAUDE.md` | `~/.claude/CLAUDE.md`                    |
| Gemini  | `examples/GEMINI.md` | `~/.gemini/GEMINI.md`                    |
| Kimi    | `examples/KIMI.md` | `~/.kimi-code/KIMI.md`                     |
| Pi      | `examples/PI.md`   | `~/.pi/PI.md`                              |
| All     | `examples/AGENTS.md` | each of the above                       |

Every variant contains the same six-phase skeleton. The differences are only:

- which slash-command prefixes the agent recognises (`/skill-name` vs
  bare `skill-name`);
- where it expects hooks and configs (the table in §4);
- whether it auto-loads `AGENTS.md` from cwd (Claude, Omni) or only from the
  home directory (Gemini, Kimi).

## 6. End-to-end example

```bash
# 1. Scaffold a hook that blocks `rm -rf` in any run_shell call
node tools/scaffold-hook.js --name no-destructive-rm \
  --event PreToolUse --matcher run_shell \
  --command "node examples/hooks/no-destructive-rm.js"

# 2. Validate
node tools/validate-hook.js hooks/no-destructive-rm.json

# 3. Install everywhere
for h in omni claude gemini kimicode; do
  node tools/install-hook.js hooks/no-destructive-rm.json --harness $h
done

# 4. Drop the plan-and-dispatch entry-point into every agent
node tools/install-agents-md.js --harness all

# 5. Audit later
node tools/audit-hooks.js --harness all
```

After step 4, every agent you open will read its entry-point file at session
start and follow the six-phase Plan → Dispatch → Verify loop automatically.