# OMNI.md — Omni Agent entry point

This file overrides `AGENTS.md` for Omni Agent sessions. Omni is the
canonical agent for which `AGENTS.md` is the fallback — keep these two
files in sync.

## Where Omni looks for skills & hooks

- **Skills:** `~/.omni/skills/<skill-name>/SKILL.md` (global) and
  `<repo>/.omni/skills/` (project). Auto-discovered at startup.
- **Extensions (custom tools):** `~/.omni/extensions/<name>.js`, registered
  in `~/.omni/omni.config.json` under `extensions`. Hot-loadable via
  `create_tool`.
- **Hooks:** `~/.omni/hooks/*.js`, registered in `omni.config.json` under
  the `hooks` key. Installed by
  `node skill-hook-creator/tools/install-hook.js --harness omni`.
- **Entry file:** `~/.omni/AGENTS.md` (or `<repo>/AGENTS.md`).

## Skill invocation convention

Omni uses slash commands (`/<skill-name>`). It also auto-loads skills
whose `description:` matches the user's prompt. To force a skill, type
`/<skill-name>` or write "Use the `<skill-name>` skill."

## Phase execution

The canonical six-phase loop in `AGENTS.md` applies verbatim. Omni-specific
reminders:

1. **Understand** — Use `/brainstorming` or `/prompt-me` before answering
   when the goal is creative or ambiguous.
2. **Explore** — Always invoke `/using-addon-skills` first when the task
   mentions a language, framework, or research target.
3. **Plan** — Use `project_todo` to write phases; mark `in_progress` and
   `done` as you go.
4. **Implement** — `apply_patch` for multi-hunk / multi-file edits;
   `edit_file` for tiny exact replacements.
5. **Verify** — `run_test` / `run_shell` / `test_coverage` /
   `security_scan` / `git_diff`. Always paste command + output.
6. **Report** — Mark todos done, then summarise: what changed, how it was
   verified, what remains.

## Hooks

Omni supports all five lifecycle events (`PreToolUse`, `PostToolUse`,
`PreInvocation`, `PostInvocation`, `Stop`). Hooks are validated by
`skill-hook-creator/tools/validate-hook.js` before install.

## Tool creation (only when asked)

When the user says "create a tool that does X", do not search memory or the
workspace — call `create_tool` directly. It writes `extensions/<name>.js`,
hot-loads it into the current session, and persists it to `omni.config.json`.
The same pattern handles skills, MCP servers, themes, and providers — see
`docs/EXTENDING.md` in the Omni install root.