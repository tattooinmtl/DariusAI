# CLAUDE.md — Claude Code entry point

This file overrides `AGENTS.md` for Claude Code sessions. The shared six-phase
plan-and-dispatch loop lives in `AGENTS.md`; this file adds Claude-specific
notes only.

## Where Claude looks for skills & hooks

- **Skills:** `~/.claude/skills/<skill-name>/SKILL.md` and project-local
  `<repo>/.claude/skills/`. Slash commands map to skill folders.
- **Hooks:** `~/.claude/hooks/*.js` and registered in
  `~/.claude/settings.json` under the `hooks` key. Installed by
  `node skill-hook-creator/tools/install-hook.js --harness claude`.
- **Project file:** `CLAUDE.md` at the repo root or `~/.claude/CLAUDE.md`.

## Skill invocation convention

Claude Code recognises slash commands. Invoke a skill with
`/<skill-name>` (e.g. `/plan`, `/coder-ai-senior-developer`). Sub-skills are
reached via the auto-loaded SKILL.md body — read it before calling nested
procedures.

## Phase execution

Follow the canonical six-phase loop in `AGENTS.md`:

1. **Understand** — `Before answering, restate the goal in one sentence.`
2. **Explore** — Prefer `lsp` / `find_symbol` for semantic answers; fall back
   to `search` / `rag_search`.
3. **Plan** — Use `project_todo action=add`; mark `in_progress` before each
   phase, `done` after verifying.
4. **Implement** — `apply_patch` for multi-file edits, `edit_file` for tiny
   exact replacements.
5. **Verify** — `run_test` / `run_shell`; paste command + output before
   claiming done.
6. **Report** — `git_diff` + concise prose.

## Hook precedence

Claude Code runs hooks in registration order. If a `PreToolUse` hook denies
a tool call, do not retry — switch approach and explain.

## Forbidden commands (blocked by global guardrails)

`rm -rf /`, `dd`, `mkfs`, `sudo rm`, `curl | sh`, `git push --force`,
`gh repo delete` — see `/global-agent-guardrails` for the full denylist.