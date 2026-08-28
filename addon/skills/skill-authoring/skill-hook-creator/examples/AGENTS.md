# AGENTS.md — Plan-and-Dispatch Workflow (canonical)

This file is read by every supported agent at session start. It defines the
six-phase loop and the skills attributed to each phase. Override per-agent
notes live in the agent-specific files (`CLAUDE.md`, `GEMINI.md`, `KIMI.md`,
`PI.md`, `OMNI.md`).

## The six phases

For every task, work through these phases in order. Skip a phase only when
it is clearly unnecessary (one-line answer → skip Plan; pure chat → skip
Implement / Verify).

### 1. UNDERSTAND
Restate the goal. If the request is ambiguous in a way that changes what
you would build, ask **one** focused question; otherwise proceed with the
reasonable interpretation and state it.

### 2. EXPLORE
Gather context BEFORE changing anything. Use:
- `using-addon-skills` — pick the right domain skill family.
- `project_inspect` — map the stack, scripts, configs.
- `rag_search` / `find_files` / `search` — locate code.
- `lsp` / `find_symbol` / `rename_symbol` — semantic answers.
- `deps` — installed vs outdated vs audit.
- `dev_env_report` / `system_info` / `where_is` — environment-shaped problems.

### 3. PLAN
For multi-step work, write the steps to `omni/todos.json` via `project_todo`;
mark the active one `in_progress`. Decide what "done" means — which tests,
build, or commands must pass.

Skills: `/plan`, `/writing-plans`, `/before-building`, `/next-decision`,
`/brainstorming`, `/prompt-me`.

### 4. IMPLEMENT
Make changes in small increments, one file at a time:
- `apply_patch` for multi-hunk / multi-file edits.
- `edit_file` for tiny exact replacements.
- `write_file` only for new files or full rewrites.
Match the conventions of the surrounding code (naming, formatting, structure).

For parallelisable independent work: `/dispatching-parallel-agents` /
`/subagent-driven-development` / `/git-worktrees`.

### 5. VERIFY
Prove the change works:
- `run_test` / `run_shell` for build, test, lint.
- `test_coverage` when coverage matters.
- `security_scan` for security-sensitive changes.
- `git_diff` to confirm the change is exactly what you intended.
- If verification fails, fix and re-verify — never report a failure as success.

Skills: `/test-driven-development`, `/systematic-debugging`,
`/verification-before-completion`.

### 6. REPORT
Mark finished todos done, then summarize concisely:
- **What changed** (files, hunk count).
- **How it was verified** (commands + output).
- **Anything left open** (follow-ups, TODOs, known gaps).

For an audit, report each finding with severity, file/line evidence, and a
proposed fix — apply fixes only when the user asked for them.

Skills: `/requesting-code-review`, `/handoff`.

## Skill → phase matrix

| Phase        | Primary skills                                                                                                |
|--------------|---------------------------------------------------------------------------------------------------------------|
| Understand   | `/brainstorming`, `/prompt-me`, `/before-building`                                                            |
| Explore      | `/using-addon-skills`, `/codebase-inspection`, `/rag_search`, `/find_symbol`, `/lsp`, `/dev_env_report`       |
| Plan         | `/plan`, `/writing-plans`, `/next-decision`, `/decisions`                                                     |
| Implement    | `/dispatching-parallel-agents`, `/subagent-driven-development`, `/git-worktrees`, `/coder-ai-senior-developer` |
| Verify       | `/test-driven-development`, `/systematic-debugging`, `/verification-before-completion`, `/vibe-code-auditor`  |
| Report       | `/requesting-code-review`, `/handoff`, `/finishing-a-development-branch`                                       |

## Hooks

If a lifecycle hook fires during this session (PreToolUse / PostToolUse /
PreInvocation / PostInvocation / Stop), treat it as authoritative — it has
already been validated by the `skill-hook-creator` skill. If you must disable
a hook, say so explicitly and re-enable it before reporting done.

## Self-check before you claim done

1. Did you read every file you edited? (Never edit unread.)
2. Did `git_diff` show exactly what you intended?
3. Did the relevant test / build / lint pass, and did you paste the output?
4. Are there TODOs the user should know about?

If any answer is "no" or "I forgot", do it now.