# KIMI.md — Kimi Code CLI entry point

This file overrides `AGENTS.md` for Kimi Code CLI sessions.

## Where Kimi looks for skills & hooks

- **Skills:** `~/.kimi-code/skills/<skill-name>/SKILL.md`. Auto-discovered at
  startup; no slash-command needed.
- **Hooks:** `~/.kimi-code/hooks/*.js`, registered in
  `~/.kimi-code/config.toml` under `[hooks]`. Installed by
  `node skill-hook-creator/tools/install-hook.js --harness kimicode`.
- **Entry file:** `~/.kimi-code/KIMI.md` (loaded once per session).

## Skill invocation convention

Kimi auto-loads skills by description match. To force a specific skill,
write: "Apply the `<skill-name>` skill here." Skills are addressed by the
`name:` frontmatter field.

## Phase execution

Same six phases as `AGENTS.md`. Differences:

1. **Understand** — Kimi prefers concise plans; one sentence per phase.
2. **Explore** — Prefer `rag_search` for keyword search across the indexed
   workspace; `lsp` may not be available — use `find_symbol`.
3. **Plan** — Use `project_todo`; keep phases ≤ 6 items.
4. **Implement** — `apply_patch` for batch edits.
5. **Verify** — Always run the smallest verifying command first (lint →
   test → build). Kimi is good at reading failures.
6. **Report** — Use the table format from `/handoff` if context is dense.

## Hook configuration

Kimi uses TOML. The hook installer writes `[hooks]` entries like:

```toml
[hooks]
paths = ["~/.kimi-code/hooks/security-guard.js"]
```

If you edit `config.toml` by hand, keep the section name `[hooks]` lowercase.