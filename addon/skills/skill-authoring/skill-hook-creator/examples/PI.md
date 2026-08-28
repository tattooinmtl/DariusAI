# PI.md — Pi Dev entry point

This file overrides `AGENTS.md` for Pi CLI sessions.

## Where Pi looks for skills & hooks

- **Skills:** `~/.pi/skills/<skill-name>/SKILL.md`. Pi discovers them at
  startup from `~/.pi/agent/extensions/` and the global skills dir.
- **Hooks:** `~/.pi/hooks/*.js`. Pi doesn't currently auto-register hooks
  via a JSON config — drop the file into the directory and reference it
  from your extension or `~/.pi/agent/extensions/index.json`. Installed by
  `node skill-hook-creator/tools/install-hook.js --harness pi`.
- **Entry file:** `~/.pi/PI.md`.

## Skill invocation convention

Pi uses bare skill names (no slash). Invoke with: "Use the
`<skill-name>` skill." Skills are addressed by folder name, which
usually matches the `name:` frontmatter.

## Phase execution

Same six phases. Pi-specific notes:

1. **Understand** — Pi is concise by default; expand only on request.
2. **Explore** — Prefer `rag_search` + `find_symbol`; `lsp` works when the
   language server is installed.
3. **Plan** — `project_todo` is the canonical todo store.
4. **Implement** — Same tools as `AGENTS.md`.
5. **Verify** — Run tests early and often; Pi is best at short feedback loops.
6. **Report** — Pi prefers short summaries; one line per file changed.

## Hook gotcha

Pi's hook field in the installer is `null` (no auto-config update). After
installing, edit `~/.pi/agent/extensions/index.json` and add the hook path
to the `hooks` array.