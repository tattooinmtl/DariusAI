# Agent Workspace Convention (`.DariusAI/`)

Companion document to `AGENTS.md`. Defines where this agent's work
artifacts live when it operates inside any project, and how the
per-day change log is kept.

`AGENTS.md` references this file at session start (see "Also read"
section near the top).

---

## 1. Pattern

When this agent — or any future agent — works on **any project**, the
work artifacts (audits, changeLogs, scratch notes, plans) live in a
hidden dot-folder named after the agent, **inside the project** the
work is happening on. Never one level up, never in a sibling mirror,
never in the user's home folder.

```
<project>/
├── AGENTS.md                      # the operating contract
├── <other project docs…>          # README, plans, source — not mine
├── .DariusAI/                     # this agent's workspace inside the project
│   ├── <DD.MM.YY>.ChangeLog.md     # running session log, append-only (§2)
│   ├── audit.md                   # any audits written for the project
│   └── <other work artifacts…>
├── .claude/                       # Claude Code's workspace, if present
├── .pi/                           # Pi's workspace, if present
├── .kimi/                         # Kimi Code's workspace, if present (rare on disk)
└── <other agent dot-folders…>     # anything else starting with "." that looks like an agent
```

---

## 2. Rules

1. **Same dot-folder name wherever this agent works.** The folder is
   always `.DariusAI/`. Same name = same agent, no guessing.
2. **Inside the project.** Not in the user's home, not next to it
   on a parallel path. The user saw this go wrong: an audit about
   thoth ended up at `C:\DariusAIWorkbench\thoth\` instead of
   `C:\.thoth\.DariusAI\`. The convention exists precisely because
   one misplaced file is enough to lose the audit trail.
3. **Read the agent-workspace tree at session start.** Every turn,
   before answering or acting, list the project's dot-folders. If
   you find one you don't recognize — `.agents`, `.pi`, `.kimi`,
   `.superpowers`, `.gemini`, anything starting with `.` followed by
   a name that looks like an agent or harness — open it, read what
   it owns, and link the relevant items from your own workspace.
4. **Append, never overwrite.** The session changeLog (§3) is
   append-only. A wrong entry stays, with a correction appended
   beneath it.
5. **Existing top-level project docs stay where they are.** This
   project's `dariusai-harnessUpdated.md`, `BrainFIX.md`, and
   `AuditFullMAX.md` predate the convention and are referenced by
   tests, the version-lock infrastructure, and external integrations.
   Do **not** move them into `.DariusAI/` — that would break tests
   and git history. New agent work goes inside the dot-folder;
   legacy project docs stay at root.

---

## 3. Cross-references

Known agent workspaces reachable from `C:/.dariusai-harness/`:

| path | owner | notes |
|---|---|---|
| `C:/.dariusai-harness/.DariusAI/` | this agent | workspace; see §4 changeLog |
| `C:/.dariusai-harness/` | — | project root, no other agent dot-folders seen yet |
| `C:/.thoth/AGENTS.md` | the thoth project's own contract | has its own §34 changeLog discipline; uses `CHANGELOG<DD.MM.YY>.md` at the project root (NOT inside a `.DariusAI/`) |
| `C:/.thoth/.claude/` | Claude Code | settings.local.json only |
| `C:/Users/ThePa/.kimi/superpowers/` | Kimi Code | the superpowers plugin skills (the agent's methodology layer, when wired) |

When working on a different project, re-list that project's dot-folders
before assuming the table above is current.

---

## 4. ChangeLog discipline (`.DariusAI/<DD.MM.YY>.ChangeLog.md`)

The changeLog is this agent's append-only session log — the per-day
record of what the agent did on the project. Distinct from
`dariusai-harnessUpdated.md` (the project-wide engineering record),
which covers the project itself.

### Filename and location

- **Path:** `<project>/.DariusAI/<DD.MM.YY>.ChangeLog.md` (one file
  per calendar day; project-path-rooted)
- **Format:** DD.MM.YY = two-digit day, two-digit month, two-digit
  year (e.g., `26.08.12.ChangeLog.md` for 12 August 2026)
- This matches the `CHANGELOG<DD.MM.YY>.md` convention used by
  `C:/.thoth/AGENTS.md §34`, modulo the leading `CHANGELOG` keyword

### Read first, every turn

Before answering or acting, read the most recent
`<DD.MM.YY>.ChangeLog.md` in the project's `.DariusAI/` folder. Do
not rely on memory from an earlier turn — entries may have been
appended between turns.

### Append-only

**NEVER erase, rewrite, or condense an existing changeLog entry.**
A wrong entry stays, with a correction appended beneath it. The
gap between planned and shipped is the most valuable thing the
record captures.

### Entry shape

``` text
## SESSION — <DD.MM.YY> — Session-N

A single bullet block. Each bullet: what was done, files changed,
validation result.
```

For multi-change days, prefer one `SESSION` block per day with
sub-bullets over multiple files. Append-only also means you append
later sessions to the same file rather than splitting.

### Code change → project version bump

When a change touches `src/**/*.py`, `src/**/static/index.html`, or
`launch.pyw`, that change also bumps the project version
(`tools/bump_version.py --minor` or `--patch` / `--set`) and
regenerates `version_lock.json`. The changeLog entry should record
the version transition alongside the work.

Doc-only changes (`AGENTS.md`, this convention, the changeLog file
itself) do **not** trigger a version bump — there is no shipped
artifact to tag.