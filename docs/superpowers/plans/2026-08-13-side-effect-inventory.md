# SP1 Arg-Validator Inventory

**Generated**: 2026-08-13 from `dariusai.agent.commands.REGISTRY`
**Source**: `docs/superpowers/specs/2026-08-13-sp1-verify-arg-commands-design.md`

## Summary

| Count | Category |
|---|---|
| **148** | Canonical commands (after de-aliasing) |
| **57** | Have non-optional `args_hint` (= `not args_hint.startswith("[")` ) |
| **51** | Of those 57 actually *reject* empty args with `Usage:` (the 7 web commands and the 5 login commands have non-optional `args_hint` but their handlers skip arg validation and return `not_implemented` instead) |
| **4** | /build /lint /format /test — shell out to a missing tool; not arg-validators |

## The 51 real arg-validators covered by SP1

Grouped, alphabetical inside each group.

### conversation (3)
- /import <path>
- /rename <name>
- /resume <session_id>

### memory (3)
- /forget <id>
- /remember <text>
- /where <topic>

### project (6)
- /init <name>
- /new <name>
- /open <name>
- /run <cmd>
- /scaffold <name> <template>
- /template <name>

### provider (6)
- /default <provider>
- /key <api-key>
- /model <name>
- /provider <name>
- /remove <name>
- /url <base-url>

### agent (1)
- /tool <name>

### git (3)
- /branch <name>
- /merge <branch>
- /pr <branch>

### files (10)
- /cd <path>
- /cp <src> <dst>
- /edit <path>
- /find <pattern>
- /grep <pattern> [path]
- /mkdir <path>
- /mv <src> <dst>
- /open-file <path>
- /read <path>
- /rm <path>
- /write <path> <content>

### settings (4)
- /config <key> <value>
- /font <name>
- /layout <name>
- /theme <name>

### skills (6)
- /create <name>
- /delete <name>
- /edit-skill <name>
- /invoke <name>
- /share-skill <name>
- /skill <name>

### permissions (4)
- /allow <cmd>
- /deny <cmd>
- /trust <path>
- /untrust <path>

### voice (2 — usage-validator only; real impl is SP4)
- /speak <text>
- /volume <level>

## The 6 false positives (have non-optional args_hint but skip validation)

These all just return `not_implemented` regardless of args. SP1 does **not**
cover them — they are SP3 (web) and SP5 (login) work.

- /web, /browse, /fetch, /youtube, /wiki, /github, /docs (SP3)
- /login, /logout, /whoami, /signup, /account (SP5)

## The 4 tool-missing errors

- /build (no `build` module)
- /lint (no ruff)
- /format (no ruff)
- /test (no pytest)

These were classified as ERRORS in the audit by `COMMAND_TEST.md` because
their handlers shell out and the subprocess fails. SP1 verifies the error
path; it does not install the tools.

## Server-side side_effects wired by SP1

Only **two** of the side_effect keys require server-side action; the rest
are correct as client-side display hints.

| Key | Where wired |
|---|---|
| `reload_llm` | `server.py:ws_chat` Task 12 — calls `build_llm(store)`, mutates `app.state.llm`, emits `llm_reloaded` event |
| `cd` | `server.py:ws_chat` Task 13 — mutates `app.state.project_dir`, persists via `store.set_setting`, emits `project_dir_changed` event |

Every other side_effect key (e.g. `set_theme`, `open_in_editor`,
`open_pr`, `set_layout`, `set_font`, `create_skill`, `invoke_skill`,
etc.) is correctly a client-side display hint and the WS handler
passes it through unchanged.
