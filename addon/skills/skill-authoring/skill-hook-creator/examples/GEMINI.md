# GEMINI.md — Gemini CLI entry point

This file overrides `AGENTS.md` for Gemini CLI sessions.

## Where Gemini looks for skills & hooks

- **Skills:** `~/.gemini/skills/<skill-name>/SKILL.md`. Gemini reads the
  `description` field to auto-trigger; the slash-command form is optional.
- **Hooks:** `~/.gemini/hooks/*.js`, registered in
  `~/.gemini/settings.json` under `hooks`. Installed by
  `node skill-hook-creator/tools/install-hook.js --harness gemini`.
- **Entry file:** `~/.gemini/GEMINI.md` (loaded at session start).

## Skill invocation convention

Gemini CLI auto-loads skills by description match; you do NOT need to type
`/skill-name`. To force a specific skill, refer to it by name in your prompt:
"Use the `plan` skill to write phases." Skills are referenced by their
`name:` frontmatter, not the folder name.

## Phase execution

The six-phase loop is identical to `AGENTS.md`. Differences for Gemini:

1. **Understand** — Gemini often returns long answers; cap with "answer in
   ≤5 lines, then expand on request".
2. **Explore** — Prefer `search` + `rag_search`; Gemini has no built-in
   language server, so `lsp` is unavailable — use `find_symbol` instead.
3. **Plan** — Gemini reads the plan from `omni/todos.json` via
   `project_todo`; treat it as the source of truth.
4. **Implement** — `apply_patch` / `edit_file` / `write_file` — same as
   `AGENTS.md`.
5. **Verify** — Gemini can run shell commands; paste the full command +
   exit code + key output.
6. **Report** — Bullet-list summary, max 8 bullets.

## Auto-trigger rules

When the user mentions: language name (Python/Rust/Go/…), framework
(React/HTMX/…), agent harness, or research target, Gemini should auto-load
`/using-addon-skills` first.