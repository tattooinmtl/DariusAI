# SP1 — Verify & wire 54 arg-required slash commands

**Date**: 2026-08-13
**Status**: draft (pending approval)
**Owner**: Kimi Code on behalf of dariusai-harness
**Companion docs**: `COMMAND_TEST.md` (audit), `src/dariusai/agent/commands.py` (registry + dispatch + handlers), `src/dariusai/viz/server.py` (WS layer)

---

## 1. Problem

The 159-command audit (`COMMAND_TEST.md`) classifies 54 commands as `ERROR`.
After reading `commands.py`, all 54 are **arg-validation handlers** — they
return `"Usage: /x <args>"` when called with no args. They DO return real
work (`ok` + `side_effect`) when called with proper args.

So the 54 are not bugs. The risk is two-layered:

1. **Handler layer** — every arg-required handler must (a) reject a
   bare invocation with a clear usage message and (b) do the right
   thing with the arg, including emitting the correct `side_effect`
   payload so the client (or WS handler) can react.
2. **WS layer** (`server.py:ws_chat`) — the handler's `side_effect`
   is forwarded to the client (`_emit_result`), but **some
   side_effects are server-side responsibilities** that the WS handler
   should perform, not the client. Examples:
   - `/cd <path>` → WS handler should update `app.state.project_dir`
     (mirroring the existing `/api/project-dir` PUT endpoint)
   - `/provider <name>` → WS handler should rebuild the LLM (the
     handler already returns `{"reload_llm": True}`; the client side
     has no LLM to reload; this needs a server-side hook)

Without this audit we cannot prove the 54 commands actually do what
they claim. The COMMAND_TEST.md audit result table masks the gap
because it only checks `status == "ok"` for `/cmd` with args, not
whether the corresponding side_effect keys reach the right layer.

## 2. Scope (in)

The 54 ERROR entries in the audit split two ways:
- **3 commands** fail because the underlying tool is missing on this
  machine: `/build` (`No module named build`), `/lint` (`ruff not
  installed`), `/format` (`ruff not installed`), `/test` (`pytest not
  installed`). These handlers do not require args — they shell out to
  a subprocess that fails before producing output. SP1 verifies the
  error path and changes nothing about the runtime toolchain.
- **51 commands** are arg-validators: when called bare they correctly
  emit `Usage: /x <args>` as their error message. SP1 verifies both
  the reject-without-args path and the success-with-args path.

The 51 arg-validators, grouped:

- **Conversation (3)**: `/resume`, `/rename`, `/import`
- **Memory (3)**: `/remember`, `/forget`, `/where`
- **Project (6)**: `/new` (+ alias `/new-project`), `/open`, `/init`,
  `/scaffold`, `/template`, `/run`
- **Provider (7)**: `/provider`, `/remove` (+ alias `/remove-provider`),
  `/model`, `/key`, `/url`, `/test-provider`, `/default`
- **Agent (3)**: `/tool`, `/review`, `/fix`
- **Git (3)**: `/branch`, `/merge`, `/pr`
- **Files (11)**: `/find`, `/grep`, `/read`, `/write`, `/edit`,
  `/open` (alias for `/open-file`), `/file-open`, `/cd`, `/mkdir`,
  `/rm`, `/cp`, `/mv`. (Treating alias pairs as one logical command:
  10 canonical + 2 aliases.)
- **Settings (4)**: `/config`, `/theme`, `/layout`, `/font`
- **Skills (6)**: `/skill`, `/invoke`, `/create`, `/edit-skill`,
  `/delete`, `/share-skill`
- **Permissions (4)**: `/trust`, `/untrust`, `/allow`, `/deny`
- **Voice (2)**: `/speak`, `/volume` (currently they reject empty args
  but always return `not_implemented` once args are present — the
  usage-validator path is in scope; the not-implemented path is SP4)
- **Login (0)**: all 5 commands already return `not_implemented` directly
  with no arg validation — out of scope for SP1, deferred to SP5.

For each command this sub-project verifies and wires:

| Check | What proves it |
|---|---|
| **a)** Handler rejects empty args with `Usage: ...` | one new unit test per command |
| **b)** Handler succeeds with valid args | one new unit test per command (mocked store / git stub) |
| **c)** Handler emits a typed `side_effect` payload (not just `ok`+`message`) | existing test suite + targeted new test where missing |
| **d)** WS handler implements server-side responsibilities where the side_effect type demands it (see §3) | one integration test per affected side_effect (`/ws/chat` driven via FastAPI TestClient) |
| **e)** Client-side index.html reacts to the side_effect | one client log/inspection per side_effect that targets the client |

## 3. Server-side responsibility split

The `side_effect` payload is currently opportunistically "fire-and-forget
to the client." Some keys MUST also be acted on by the WS handler — the
client has no way to perform them. The decision rule:

> If fulfilling the side_effect requires access to server-only state
> (the LLM, the file system root, the active provider store), the WS
> handler performs it. Pure display-side effects (open a panel, render
> a list) still go to the client.

Concrete assignments for SP1:

| Side-effect key | Layer | SP1 action |
|---|---|---|
| `{"reload_llm": True}` | **server** (rebuild chat session's LLM) | wire in `ws_chat` after `_run_command`; emit `llm_reloaded` event to client |
| `{"cd": path}` | **server** (set `app.state.project_dir` + settings) | wire in `ws_chat` (mirrors `/api/project-dir` PUT) |
| `{"open_in_editor": path}` | client (file-edit panel) | out of scope (client only) |
| `{"open_project": name}` / `{"create_project": ...}` / `{"close_project": True}` / `{"list_projects": True}` | client (workbench panel) | out of scope (client only) |
| `{"set_active_provider": ...}`-like keys | server (call `store.set_active_provider`) | out of scope — already done in `/provider` handler |
| `{"set_layout": ...}`, `{"set_theme": ...}`, `{"set_font": ...}` | client (CSS swap) | out of scope (client only) |
| `{"set_setting": ...}` | server (call `store.set_setting`) | already done in `/_cmd_config` |
| `{"create_skill": ...}`, `{"edit_skill": ...}`, `{"delete_skill": ...}` | client (opens skill editor panel) | out of scope (client only) |
| `{"invoke_skill": ...}` | server (push to chat turn) | out of scope for SP1 (chat-session API) |

**Net result**: SP1 needs to wire exactly two side_effects server-side:
`reload_llm` and `cd`. The other side_effects are correctly client-only
or already handled in the handler.

## 4. Out of scope

- The 19 `not_implemented` commands (SP3 + SP4 + SP5)
- Replacing the placeholder "WORKING" commands (SP2)
- New client-side panels or UI wiring beyond what already exists
- Refactoring `commands.py` layout or the WS dispatch flow

## 5. Approach

**Path A (recommended)**: One unified audit pass + targeted fixes.

1. For each of the 54 commands:
   - add one "rejects empty args" test (already done in spirit for some)
   - add one "happy-path with valid args" test (mocked store)
   - record the `side_effect` key in a single audit doc
2. For the 2 server-side side_effects, add a thin decorator in
   `ws_chat` that consumes `reload_llm` and `cd` and performs them
   after `_run_command` returns.
3. Run `pytest tests/test_chat_commands.py` + the new tests; the
   suite must show one row per command with pass/fail.
4. Update `COMMAND_TEST.md` (regenerate) so the "ERROR" row turns
   into a "PASS" row for each command when called with valid args.

**Path B**: Hand-verify per command from a curl script, no tests.
Faster, but no regression safety; contradicts AGENTS.md §16.

## 6. Files

| File | Change |
|---|---|
| `tests/test_chat_commands.py` | extend from 16 to ~120 tests: 51 commands × 2 ("Usage" reject + "happy" with args) + 3 tool-missing error tests + alias-coverage tests = ~120 total |
| `src/dariusai/viz/server.py` | add a 5–10 line block in `ws_chat` after `_run_command` that consumes `reload_llm` + `cd` from the result |
| `src/dariusai/agent/commands.py` | no handler changes — only an audit pass; doc string added to one handler that needs server-side support |
| `COMMAND_TEST.md` | regenerated to show arg-passing success for each command |

No new dependencies. No `pyproject.toml` changes.

## 7. Validation

1. `pytest tests/test_chat_commands.py tests/test_chat_websocket.py tests_test_version.py tests/test_version_lock.py` — all must pass.
2. Drive `ws_chat` end-to-end with FastAPI's `TestClient` for at least
   the two server-side side_effects (`/cd foo`, `/provider bar`).
3. Regenerate `COMMAND_TEST.md` and confirm all 54 rows now show
   "WORKING / ok" when called with valid args.

## 8. Risks

- Some commands are platform-dependent (e.g. `/git` runs `git`
  subprocess). The audit must mock those or guard on Windows.
- `MockStore` in the existing tests does not cover every store
  method (e.g. `delete_skill`). Expanding the mock or relaxing tests
  by command class.
- WS-side test harness may hang in teardown (pre-existing in
  `test_chat_websocket.py`). Mitigation: keep server-side wiring
  tested via the handler contract, not via raw WS teardown.

## 9. Acceptance criteria

- All 54 commands have a passing happy-path test.
- All 54 commands have a passing "Usage" rejection test.
- `side_effect` audit table is committed and matches the registry.
- `ws_chat` correctly performs `reload_llm` and `cd`.
- `COMMAND_TEST.md` regenerated; the 54 rows read "WORKING / ok" for
  the valid-args invocation.
- Version bumped per AGENTS.md §24 (`tools/bump_version.py --minor`).

## 10. After SP1

Move to **SP2** (replace placeholder WORKING commands with real
implementations, ~20 commands, ~400–800 lines + tests).
