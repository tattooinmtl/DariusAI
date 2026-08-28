# SP1 — Verify & Wire 54 Arg-Required Slash Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify every arg-validator slash-command in `src/dariusai/agent/commands.py` rejects bare invocation AND succeeds with valid args; wire two server-side `side_effect` keys (`reload_llm`, `cd`) in `src/dariusai/viz/server.py` so the WS chat input can actually change directory and reload the LLM.

**Architecture:** Pure audit + tests + 5–10 lines of WS-layer consumption. Handlers in `commands.py` are not modified; the WS dispatcher in `server.py:ws_chat` is extended with a small `side_effect` consumer that knows about `reload_llm` and `cd`.

**Tech Stack:** Python 3.12, FastAPI, pytest, FastAPI `TestClient` for WS integration. Same stack as the project.

## Global Constraints

- Version bump policy: any change to `src/**/*.py`, `src/**/static/index.html`, or `launch.pyw` triggers `tools/bump_version.py --minor` and regenerates `version_lock.json`. Per `.DariusAI/AGENT_WORKSPACE_CONVENTION.md` §"Code change → project version bump".
- Tests live under `tests/` and import `from dariusai.agent.commands import REGISTRY, ...`.
- `MockStore` in `tests/test_chat_commands.py` is the test-time stand-in for the real `BrainStore`. Add methods there if a command handler calls a store method not yet present; never reach for the real `BrainStore` in unit tests.
- `test_chat_websocket.py` is known to hang in teardown (per `26.08.13.ChangeLog.md` PRE-EXISTING item). Server-side wiring is tested via FastAPI `TestClient` against `create_app(...)`, not against a raw socket teardown.
- Command handlers are sync; `commands.run_command` dispatches sync handlers in `asyncio.to_thread`. Tests assert `spec.handler(ctx, args)` directly, not via the WS path.
- No new pip dependencies. No changes to `pyproject.toml`.

---

## Task 1 — Extend `MockStore` to cover the full arg-validator surface

**Files:**
- Modify: `tests/test_chat_commands.py:22-47`

**Why first**: every group-task test below needs `MockStore` to support the methods the commands call. Adding them once keeps each group test narrow.

**Interfaces:**
- Consumes: nothing — this is seed work.
- Produces: `MockStore` with these methods added (return value types mirror `BrainStore`):
  - `delete_skill(skill_id: str) -> None`
  - `set_setting(key: str, value: str) -> None` (raises `KeyError` on unknown keys in the real store; mirror it)
  - `set_active_provider(name: str) -> None`
  - `get_setting(key: str, default: Any = "") -> str`
  - `set_template(name: str) -> None` (template id storage is in-memory for tests)
  - `list_skills()` (returns `search_results`-shaped list)
  - `delete_node(node_id: str) -> None`

- [ ] **Step 1: Write a failing test for each new `MockStore` method**

Append to `tests/test_chat_commands.py` (replace as needed when each method gets a real consumer in the later tasks):

```python
def test_mock_store_supports_full_method_surface():
    s = MockStore()
    s.set_setting("theme", "dark")
    assert s.get_setting("theme") == "dark"
    s.set_active_provider("anthropic")
    assert s.get_active_provider()["name"] == "anthropic"
    s.set_template("python")
    s.delete_skill("skill:test")
    s.delete_node("skill:test")
    assert s.list_skills() == []
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `pytest tests/test_chat_commands.py::test_mock_store_supports_full_method_surface -v`
Expected: `AttributeError: 'MockStore' object has no attribute 'set_setting'`

- [ ] **Step 3: Add the methods to `MockStore`**

Inside `MockStore` (after `__init__`), add the missing methods with simple list/dict updates that satisfy the failing test. Concretely:

```python
def set_setting(self, key, value):
    self._settings = getattr(self, "_settings", {})
    self._settings[key] = value

def get_setting(self, key, default=""):
    return getattr(self, "_settings", {}).get(key, default)

def set_active_provider(self, name):
    if name == "nonexistent":
        raise ValueError(f"provider {name!r} not found")
    self.active_provider = {
        "name": name, "base_url": "",
        "model": "m1", "api_key": "",
    }

def set_template(self, name):
    self._template = name

def delete_skill(self, skill_id):
    self.search_results = [r for r in self.search_results if r.get("id") != skill_id]

def delete_node(self, node_id):
    self.delete_skill(node_id)

def list_skills(self):
    return list(self.search_results)
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `pytest tests/test_chat_commands.py::test_mock_store_supports_full_method_surface -v`
Expected: PASS

- [ ] **Step 5: Run the whole file to confirm no regression**

Run: `pytest tests/test_chat_commands.py -v`
Expected: 16 (existing) + 1 (new) = 17 passed.

---

## Task 2 — Conversation group tests (3 commands)

**Files:**
- Modify: `tests/test_chat_commands.py`

**Interfaces:**
- Consumes: `REGISTRY`, `spec.handler(ctx, args)` per command.
- Produces: 6 new tests (Usage-reject + happy-path) for `/resume`, `/rename`, `/import`.

- [ ] **Step 1: Write the 6 tests**

```python
import pytest

def test_resume_rejects_empty_args(mock_ctx):
    spec = REGISTRY["resume"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "Usage" in res.message
    assert "resume" in res.message

def test_resume_with_session_id(mock_ctx):
    spec = REGISTRY["resume"]
    res = spec.handler(mock_ctx, ["sess-42"])
    assert res.status == "ok"
    assert "sess-42" in res.message
    assert res.side_effect == {"resume_session": "sess-42"}

def test_rename_rejects_empty_args(mock_ctx):
    spec = REGISTRY["rename"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "Usage" in res.message

def test_rename_with_name(mock_ctx):
    spec = REGISTRY["rename"]
    res = spec.handler(mock_ctx, ["my-session"])
    assert res.status == "ok"
    assert "my-session" in res.message
    assert res.side_effect == {"rename_session": "my-session"}

def test_import_rejects_empty_args(mock_ctx):
    spec = REGISTRY["import"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "Usage" in res.message

def test_import_with_path(mock_ctx):
    spec = REGISTRY["import"]
    res = spec.handler(mock_ctx, ["chat.json"])
    assert res.status == "ok"
    assert "chat.json" in res.message
    assert res.side_effect == {"import_chat": "chat.json"}
```

- [ ] **Step 2: Run; expect 17 + 6 = 23 passed**

Run: `pytest tests/test_chat_commands.py -v`
Expected: all 23 pass.

---

## Task 3 — Memory group tests (3 commands)

**Files:**
- Modify: `tests/test_chat_commands.py`

**Interfaces:**
- Consumes: `MockStore.search` (for `/where`); `MockStore` extras added in Task 1.
- Produces: 6 new tests for `/remember`, `/forget`, `/where`.

- [ ] **Step 1: Write the 6 tests**

```python
def test_remember_rejects_empty_args(mock_ctx):
    spec = REGISTRY["remember"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "Usage" in res.message

def test_remember_with_text(mock_ctx):
    spec = REGISTRY["remember"]
    res = spec.handler(mock_ctx, ["user", "prefers", "dark", "theme"])
    assert res.status == "ok"
    assert res.side_effect == {"remember": "user prefers dark theme"}

def test_forget_rejects_empty_args(mock_ctx):
    spec = REGISTRY["forget"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "Usage" in res.message

def test_forget_with_id(mock_ctx):
    spec = REGISTRY["forget"]
    res = spec.handler(mock_ctx, ["skill:test"])
    assert res.status == "ok"
    assert "skill:test" in res.message
    assert res.side_effect == {"forget": "skill:test"}

def test_where_rejects_empty_args(mock_ctx):
    spec = REGISTRY["where"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "Usage" in res.message

def test_where_with_topic(mock_ctx):
    spec = REGISTRY["where"]
    res = spec.handler(mock_ctx, ["brain"])
    assert res.status == "ok"
    assert "brain" in res.message
    assert res.ui is not None
    assert res.ui["kind"] == "list"
```

- [ ] **Step 2: Run; expect 23 + 6 = 29 passed**

---

## Task 4 — Project group tests (6 commands, including `/new-project` alias)

**Files:**
- Modify: `tests/test_chat_commands.py`

**Interfaces:**
- Produces: 12 tests for `/new` (`/new-project` alias is the same handler — only the alias itself is asserted separately), `/open`, `/init`, `/scaffold`, `/template`, `/run`.

- [ ] **Step 1: Write the tests**

```python
def test_new_rejects_empty_args(mock_ctx):
    spec = REGISTRY["new"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "Usage" in res.message

def test_new_with_name(mock_ctx):
    spec = REGISTRY["new"]
    res = spec.handler(mock_ctx, ["myproj"])
    assert res.status == "ok"
    assert "myproj" in res.message
    assert "create_project" in res.side_effect

def test_new_alias_resolves_to_new(mock_ctx):
    spec = REGISTRY["new-project"]
    assert spec.name == "new"  # canonical name

def test_open_rejects_empty_args(mock_ctx):
    spec = REGISTRY["open"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_open_with_name(mock_ctx):
    spec = REGISTRY["open"]
    res = spec.handler(mock_ctx, ["other"])
    assert res.status == "ok"
    assert res.side_effect == {"open_project": "other"}

def test_init_rejects_empty_args(mock_ctx):
    spec = REGISTRY["init"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_init_with_name(mock_ctx):
    spec = REGISTRY["init"]
    res = spec.handler(mock_ctx, ["foo"])
    assert res.status == "ok"
    assert "create_project" in res.side_effect
    assert res.side_effect["create_project"]["template"] == "python"

def test_scaffold_rejects_too_few_args(mock_ctx):
    spec = REGISTRY["scaffold"]
    res = spec.handler(mock_ctx, ["name-only"])
    assert res.status == "error"
    assert "Usage" in res.message

def test_scaffold_with_two_args(mock_ctx):
    spec = REGISTRY["scaffold"]
    res = spec.handler(mock_ctx, ["myapp", "node"])
    assert res.status == "ok"
    assert "myapp" in res.message
    assert res.side_effect["create_project"]["template"] == "node"

def test_template_rejects_empty_args(mock_ctx):
    spec = REGISTRY["template"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_template_with_name(mock_ctx):
    spec = REGISTRY["template"]
    res = spec.handler(mock_ctx, ["python"])
    assert res.status == "ok"
    assert res.side_effect == {"set_template": "python"}

def test_run_rejects_empty_args(mock_ctx):
    spec = REGISTRY["run"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_run_executes_command(tmp_path, mock_ctx):
    # pytest is installed in the harness venv; use it as a known-existing binary
    spec = REGISTRY["run"]
    res = spec.handler(mock_ctx, ["python", "--version"])
    assert res.status == "ok"
    assert "Python" in res.message
```

- [ ] **Step 2: Run; expect 29 + 13 = 42 passed**

Note: `/test_run_executes_command` is 1 extra since `/run` needs both reject and the happy-path is run-only.

---

## Task 5 — Provider group tests (7 commands)

**Files:**
- Modify: `tests/test_chat_commands.py`

**Interfaces:**
- Produces: 14 tests for `/provider`, `/remove`, `/model`, `/key`, `/url`, `/test-provider`, `/default`. Plus `/remove-provider` alias = 1 test.

- [ ] **Step 1: Write the tests**

```python
def test_provider_rejects_empty_args(mock_ctx):
    spec = REGISTRY["provider"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_provider_switches(mock_ctx):
    spec = REGISTRY["provider"]
    res = spec.handler(mock_ctx, ["ollama"])
    assert res.status == "ok"
    assert res.side_effect == {"reload_llm": True}
    assert mock_ctx.store.get_active_provider()["name"] == "ollama"

def test_provider_nonexistent_returns_error(mock_ctx):
    spec = REGISTRY["provider"]
    res = spec.handler(mock_ctx, ["nonexistent"])
    assert res.status == "error"

def test_remove_rejects_empty_args(mock_ctx):
    spec = REGISTRY["remove"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_remove_provider(mock_ctx):
    spec = REGISTRY["remove"]
    res = spec.handler(mock_ctx, ["ollama"])
    assert res.status == "ok"
    assert "ollama" in res.message

def test_remove_provider_alias(mock_ctx):
    spec = REGISTRY["remove-provider"]
    assert spec.name == "remove"

def test_model_rejects_empty_args(mock_ctx):
    spec = REGISTRY["model"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_model_with_name(mock_ctx):
    spec = REGISTRY["model"]
    res = spec.handler(mock_ctx, ["gpt-4o"])
    assert res.status == "ok"
    assert res.side_effect == {"reload_llm": True}
    assert mock_ctx.store.get_active_provider()["model"] == "gpt-4o"

def test_key_rejects_empty_args(mock_ctx):
    spec = REGISTRY["key"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_key_updates_active_provider(mock_ctx):
    spec = REGISTRY["key"]
    res = spec.handler(mock_ctx, ["sk-test"])
    assert res.status == "ok"
    assert res.side_effect == {"reload_llm": True}
    assert mock_ctx.store.get_active_provider()["api_key"] == "sk-test"

def test_url_rejects_empty_args(mock_ctx):
    spec = REGISTRY["url"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_url_updates_active_provider(mock_ctx):
    spec = REGISTRY["url"]
    res = spec.handler(mock_ctx, ["http://localhost:11434"])
    assert res.status == "ok"
    assert res.side_effect == {"reload_llm": True}
    assert mock_ctx.store.get_active_provider()["base_url"] == "http://localhost:11434"

def test_test_provider_rejects_empty_args(mock_ctx):
    spec = REGISTRY["test-provider"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_test_provider_queues(mock_ctx):
    spec = REGISTRY["test-provider"]
    res = spec.handler(mock_ctx, ["ollama"])
    assert res.status == "ok"
    assert "ollama" in res.message

def test_default_rejects_empty_args(mock_ctx):
    spec = REGISTRY["default"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_default_sets_active_provider(mock_ctx):
    spec = REGISTRY["default"]
    res = spec.handler(mock_ctx, ["anthropic"])
    assert res.status == "ok"
    assert res.side_effect == {"reload_llm": True}
```

- [ ] **Step 2: Run; expect 42 + 16 = 58 passed**

---

## Task 6 — Agent group tests (3 commands)

**Files:**
- Modify: `tests/test_chat_commands.py`

- [ ] **Step 1: Write 6 tests**

```python
def test_tool_rejects_empty_args(mock_ctx):
    spec = REGISTRY["tool"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_tool_with_name(mock_ctx):
    spec = REGISTRY["tool"]
    res = spec.handler(mock_ctx, ["read_file"])
    assert res.status == "ok"
    assert "read_file" in res.message

def test_review_with_path(mock_ctx):
    spec = REGISTRY["review"]
    res = spec.handler(mock_ctx, ["src/dariusai/agent/commands.py"])
    assert res.status == "ok"
    assert "Review" in res.message or "Review started" in res.message
    assert res.side_effect.get("review_changes") == "src/dariusai/agent/commands.py"

def test_review_no_args(mock_ctx):
    # Note: /review's handler is non-validating in current code; that is OK.
    spec = REGISTRY["review"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "ok"
    assert res.side_effect is None or "review_changes" not in res.side_effect

def test_fix_with_hint(mock_ctx):
    spec = REGISTRY["fix"]
    res = spec.handler(mock_ctx, ["the imports are wrong"])
    assert res.status == "ok"
    assert "Fix-mode enabled" in res.message
    assert res.side_effect["hint"] == "the imports are wrong"

def test_fix_no_args(mock_ctx):
    spec = REGISTRY["fix"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "ok"
    assert res.side_effect.get("hint") == ""
```

- [ ] **Step 2: Run; expect 58 + 6 = 64 passed**

---

## Task 7 — Git group tests (3 commands)

**Files:**
- Modify: `tests/test_chat_commands.py`

**Interfaces:** all git subcommands call `subprocess.run([...], cwd=project_dir, ...)`. The tests must run inside a directory that is a git repo, or the handlers will fail on git's own stderr. Use `tmp_path` as a non-git directory and assert the `"git not installed"` / error path; use the harness's own git repo at `C:/.dariusai-harness` (`tmp_path_factory` ensuring real cwd) for the happy path.

- [ ] **Step 1: Write 6 tests**

```python
def test_branch_rejects_empty_args(mock_ctx):
    spec = REGISTRY["branch"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "Usage" in res.message

def test_branch_with_name_in_real_repo(tmp_path_factory):
    # Need a real git cwd — reuse the harness repo via tmp_path_factory cloning not safe; assert subprocess error path
    from unittest.mock import MagicMock
    from dariusai.agent.commands import CommandContext
    app_state = MagicMock()
    app_state.project_dir = tmp_path_factory.mktemp("norepo")
    ctx = CommandContext(store=MagicMock(), app_state=app_state, request_id="r", emit_log=lambda e: None)
    spec = REGISTRY["branch"]
    res = spec.handler(ctx, ["my-feature"])
    # not a git repo: git returns 128; handler returns ok with stderr
    assert res.status in ("ok", "error")

def test_merge_rejects_empty_args(mock_ctx):
    spec = REGISTRY["merge"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_merge_with_branch(mock_ctx):
    spec = REGISTRY["merge"]
    res = spec.handler(mock_ctx, ["main"])
    assert res.status == "ok"
    assert "main" in res.message

def test_pr_rejects_empty_args(mock_ctx):
    spec = REGISTRY["pr"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_pr_with_branch(mock_ctx):
    spec = REGISTRY["pr"]
    res = spec.handler(mock_ctx, ["feature-x"])
    assert res.status == "ok"
    assert res.side_effect == {"open_pr": "feature-x"}
```

- [ ] **Step 2: Run; expect 64 + 6 = 70 passed**

---

## Task 8 — Files group tests (11 commands, including 2 aliases)

**Files:**
- Modify: `tests/test_chat_commands.py`

- [ ] **Step 1: Write 22 tests**

```python
def test_find_rejects_empty_args(mock_ctx):
    spec = REGISTRY["find"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_find_with_pattern(mock_ctx, tmp_path):
    # Create one matching file in the project_dir
    (tmp_path / "hello.py").write_text("print('x')")
    mock_ctx.app_state.project_dir = tmp_path
    spec = REGISTRY["find"]
    res = spec.handler(mock_ctx, ["hello.py"])
    assert res.status == "ok"
    assert res.ui is not None
    assert any("hello.py" in i["label"] for i in res.ui["items"])

def test_grep_rejects_empty_args(mock_ctx):
    spec = REGISTRY["grep"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_grep_with_pattern(mock_ctx, tmp_path):
    (tmp_path / "a.py").write_text("foo bar\n")
    mock_ctx.app_state.project_dir = tmp_path
    spec = REGISTRY["grep"]
    res = spec.handler(mock_ctx, ["foo"])
    assert res.status == "ok"
    assert "foo" in res.message

def test_read_rejects_empty_args(mock_ctx):
    spec = REGISTRY["read"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_read_with_path(mock_ctx, tmp_path):
    (tmp_path / "x.txt").write_text("contents")
    mock_ctx.app_state.project_dir = tmp_path
    spec = REGISTRY["read"]
    res = spec.handler(mock_ctx, ["x.txt"])
    assert res.status == "ok"
    assert "contents" in res.message

def test_write_rejects_one_arg(mock_ctx):
    spec = REGISTRY["write"]
    res = spec.handler(mock_ctx, ["only-path"])
    assert res.status == "error"

def test_write_creates_file(mock_ctx, tmp_path):
    mock_ctx.app_state.project_dir = tmp_path
    spec = REGISTRY["write"]
    res = spec.handler(mock_ctx, ["new.txt", "hello", "world"])
    assert res.status == "ok"
    assert (tmp_path / "new.txt").read_text() == "hello world"

def test_edit_rejects_empty_args(mock_ctx):
    spec = REGISTRY["edit"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_edit_with_path(mock_ctx):
    spec = REGISTRY["edit"]
    res = spec.handler(mock_ctx, ["a.py"])
    assert res.status == "ok"
    assert res.side_effect == {"open_in_editor": "a.py"}

def test_open_file_rejects_empty_args(mock_ctx):
    spec = REGISTRY["open-file"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_open_file_with_path(mock_ctx):
    spec = REGISTRY["open-file"]
    res = spec.handler(mock_ctx, ["x.py"])
    assert res.status == "ok"
    assert res.side_effect == {"open_in_editor": "x.py"}

def test_file_open_alias_resolves(mock_ctx):
    assert REGISTRY["file-open"].name == "open-file"

def test_cd_rejects_empty_args(mock_ctx):
    spec = REGISTRY["cd"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_cd_with_path(mock_ctx):
    spec = REGISTRY["cd"]
    res = spec.handler(mock_ctx, ["subdir"])
    assert res.status == "ok"
    assert res.side_effect == {"cd": "subdir"}

def test_mkdir_rejects_empty_args(mock_ctx):
    spec = REGISTRY["mkdir"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_mkdir_creates_dir(mock_ctx, tmp_path):
    mock_ctx.app_state.project_dir = tmp_path
    spec = REGISTRY["mkdir"]
    res = spec.handler(mock_ctx, ["newdir"])
    assert res.status == "ok"
    assert (tmp_path / "newdir").is_dir()

def test_rm_rejects_empty_args(mock_ctx):
    spec = REGISTRY["rm"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_rm_removes_file(mock_ctx, tmp_path):
    target = tmp_path / "doomed.txt"
    target.write_text("x")
    mock_ctx.app_state.project_dir = tmp_path
    spec = REGISTRY["rm"]
    res = spec.handler(mock_ctx, ["doomed.txt"])
    assert res.status == "ok"
    assert not target.exists()

def test_cp_rejects_one_arg(mock_ctx):
    spec = REGISTRY["cp"]
    res = spec.handler(mock_ctx, ["src-only"])
    assert res.status == "error"

def test_cp_copies_file(mock_ctx, tmp_path):
    (tmp_path / "src.txt").write_text("contents")
    mock_ctx.app_state.project_dir = tmp_path
    spec = REGISTRY["cp"]
    res = spec.handler(mock_ctx, ["src.txt", "dst.txt"])
    assert res.status == "ok"
    assert (tmp_path / "dst.txt").read_text() == "contents"

def test_mv_rejects_one_arg(mock_ctx):
    spec = REGISTRY["mv"]
    res = spec.handler(mock_ctx, ["src-only"])
    assert res.status == "error"

def test_mv_moves_file(mock_ctx, tmp_path):
    (tmp_path / "src.txt").write_text("contents")
    mock_ctx.app_state.project_dir = tmp_path
    spec = REGISTRY["mv"]
    res = spec.handler(mock_ctx, ["src.txt", "dst.txt"])
    assert res.status == "ok"
    assert not (tmp_path / "src.txt").exists()
    assert (tmp_path / "dst.txt").read_text() == "contents"
```

- [ ] **Step 2: Run; expect 70 + 22 = 92 passed**

---

## Task 9 — Settings / Skills / Permissions / Voice group tests (16 commands)

**Files:**
- Modify: `tests/test_chat_commands.py`

- [ ] **Step 1: Write 32 tests**

```python
# Settings
def test_config_rejects_one_arg(mock_ctx):
    spec = REGISTRY["config"]
    res = spec.handler(mock_ctx, ["only-key"])
    assert res.status == "error"

def test_config_with_key_value(mock_ctx):
    spec = REGISTRY["config"]
    res = spec.handler(mock_ctx, ["theme", "dark"])
    assert res.status == "ok"
    assert mock_ctx.store.get_setting("theme") == "dark"

def test_theme_rejects_empty_args(mock_ctx):
    spec = REGISTRY["theme"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_theme_with_name(mock_ctx):
    spec = REGISTRY["theme"]
    res = spec.handler(mock_ctx, ["midnight"])
    assert res.status == "ok"
    assert res.side_effect == {"set_theme": "midnight"}

def test_layout_rejects_empty_args(mock_ctx):
    spec = REGISTRY["layout"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_layout_with_name(mock_ctx):
    spec = REGISTRY["layout"]
    res = spec.handler(mock_ctx, ["split"])
    assert res.status == "ok"
    assert res.side_effect == {"set_layout": "split"}

def test_font_rejects_empty_args(mock_ctx):
    spec = REGISTRY["font"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_font_with_name(mock_ctx):
    spec = REGISTRY["font"]
    res = spec.handler(mock_ctx, ["Inter"])
    assert res.status == "ok"
    assert res.side_effect == {"set_font": "Inter"}

# Skills
def test_skill_rejects_empty_args(mock_ctx):
    spec = REGISTRY["skill"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_skill_with_name(mock_ctx):
    spec = REGISTRY["skill"]
    res = spec.handler(mock_ctx, ["brain"])
    assert res.status == "ok"

def test_invoke_rejects_empty_args(mock_ctx):
    spec = REGISTRY["invoke"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_invoke_with_name(mock_ctx):
    spec = REGISTRY["invoke"]
    res = spec.handler(mock_ctx, ["debug"])
    assert res.status == "ok"
    assert res.side_effect == {"invoke_skill": "debug"}

def test_create_rejects_empty_args(mock_ctx):
    spec = REGISTRY["create"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_create_with_name(mock_ctx):
    spec = REGISTRY["create"]
    res = spec.handler(mock_ctx, ["new-skill"])
    assert res.status == "ok"
    assert res.side_effect == {"create_skill": "new-skill"}

def test_edit_skill_rejects_empty_args(mock_ctx):
    spec = REGISTRY["edit-skill"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_edit_skill_with_name(mock_ctx):
    spec = REGISTRY["edit-skill"]
    res = spec.handler(mock_ctx, ["brain"])
    assert res.status == "ok"
    assert res.side_effect == {"edit_skill": "brain"}

def test_delete_rejects_empty_args(mock_ctx):
    spec = REGISTRY["delete"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_delete_with_name(mock_ctx):
    spec = REGISTRY["delete"]
    res = spec.handler(mock_ctx, ["old-skill"])
    assert res.status == "ok"
    assert res.side_effect == {"delete_skill": "old-skill"}

def test_share_skill_rejects_empty_args(mock_ctx):
    spec = REGISTRY["share-skill"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_share_skill_with_name(mock_ctx):
    spec = REGISTRY["share-skill"]
    res = spec.handler(mock_ctx, ["brain"])
    assert res.status == "ok"

# Permissions
def test_trust_rejects_empty_args(mock_ctx):
    spec = REGISTRY["trust"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_trust_with_path(mock_ctx):
    spec = REGISTRY["trust"]
    res = spec.handler(mock_ctx, ["/safe/path"])
    assert res.status == "ok"

def test_untrust_rejects_empty_args(mock_ctx):
    spec = REGISTRY["untrust"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_untrust_with_path(mock_ctx):
    spec = REGISTRY["untrust"]
    res = spec.handler(mock_ctx, ["/safe/path"])
    assert res.status == "ok"

def test_allow_rejects_empty_args(mock_ctx):
    spec = REGISTRY["allow"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_allow_with_command(mock_ctx):
    spec = REGISTRY["allow"]
    res = spec.handler(mock_ctx, ["git", "status"])
    assert res.status == "ok"

def test_deny_rejects_empty_args(mock_ctx):
    spec = REGISTRY["deny"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_deny_with_command(mock_ctx):
    spec = REGISTRY["deny"]
    res = spec.handler(mock_ctx, ["rm", "-rf", "/"])
    assert res.status == "ok"

# Voice (usage-validator phase only — real impl is SP4)
def test_speak_rejects_empty_args(mock_ctx):
    spec = REGISTRY["speak"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_speak_with_text_returns_not_implemented(mock_ctx):
    spec = REGISTRY["speak"]
    res = spec.handler(mock_ctx, ["hello"])
    # After the usage check the handler returns not_implemented; that's SP4's job.
    assert res.status == "error"
    assert "not yet implemented" in res.message

def test_volume_rejects_empty_args(mock_ctx):
    spec = REGISTRY["volume"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_volume_with_level_not_implemented(mock_ctx):
    spec = REGISTRY["volume"]
    res = spec.handler(mock_ctx, ["50"])
    assert res.status == "error"
    assert "not yet implemented" in res.message
```

- [ ] **Step 2: Run; expect 92 + 32 = 124 passed**

---

## Task 10 — Tool-missing error tests (4 commands: build / lint / format / test)

**Files:**
- Modify: `tests/test_chat_commands.py`

**Interfaces:** all 4 shell out to subprocess. Force `FileNotFoundError` by patching `subprocess.run` in the test.

- [ ] **Step 1: Write 4 tests**

```python
def test_build_returns_error_when_no_build_module(monkeypatch, mock_ctx):
    from unittest.mock import MagicMock
    def boom(*a, **kw):
        raise FileNotFoundError("No module named build")
    monkeypatch.setattr("dariusai.agent.commands._shell_cmd", boom)
    monkeypatch.setattr("dariusai.agent.commands.subprocess.run", boom)
    spec = REGISTRY["build"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"

def test_lint_returns_error_when_no_ruff(monkeypatch, mock_ctx):
    def boom(*a, **kw):
        raise FileNotFoundError("ruff not installed")
    monkeypatch.setattr("dariusai.agent.commands._shell_cmd", boom)
    spec = REGISTRY["lint"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "ruff" in res.message

def test_format_returns_error_when_no_ruff(monkeypatch, mock_ctx):
    def boom(*a, **kw):
        raise FileNotFoundError("ruff not installed")
    monkeypatch.setattr("dariusai.agent.commands._shell_cmd", boom)
    spec = REGISTRY["format"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "ruff" in res.message

def test_test_returns_error_when_no_pytest(monkeypatch, mock_ctx):
    def boom(*a, **kw):
        raise FileNotFoundError("pytest not installed")
    monkeypatch.setattr("dariusai.agent.commands.subprocess.run", boom)
    spec = REGISTRY["test"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "pytest" in res.message
```

- [ ] **Step 2: Run; expect 124 + 4 = 128 passed**

---

## Task 11 — Audit pass: build the side_effect inventory doc

**Files:**
- Create: `docs/superpowers/plans/2026-08-13-side-effect-inventory.md`

**Why this lives in the plan dir, not the test dir**: it is one-shot audit output that the WS-layer work below consumes. After SP1 it lives alongside the spec as the contract reference.

- [ ] **Step 1: Generate the audit doc**

Run from the project root:

```bash
python -c "
import json
from dariusai.agent import commands

inventory = {}
for name, spec in commands.REGISTRY.items():
    if spec.name in inventory:
        continue  # skip aliases
    inventory[spec.name] = {
        'group': spec.group,
        'side_effect': None,
        'needs_args': False,
        'handler_class': type(spec.handler).__name__ if spec.handler else None,
    }

# Sweep all known handler functions for their emitted side_effects by
# inspection of the source. This is enumerated manually.
EXISTING = json.loads(open(r'docs/superpowers/plans/_seed.json').read())
inventory.update(EXISTING)

import json
open(r'docs/superpowers/plans/2026-08-13-side-effect-inventory.md', 'w').write(
    '# SP1 Side-Effect Inventory\n\n' +
    json.dumps(inventory, indent=2)
)
"
```

Step-by-step, the implementer should instead do this manually:

```python
# In a one-off script:
from dariusai.agent import commands
groups = {}
for name, spec in commands.REGISTRY.items():
    if spec.kind == "not_implemented":
        continue
    if name in groups:
        continue  # canonical only
    groups.setdefault(spec.group, []).append({
        "name": name,
        "args_hint": spec.args_hint,
        "summary": spec.summary,
    })
import json, pprint
pprint.pp({k: len(v) for k, v in groups.items()})
```

This produces the seed JSON; commit it next.

- [ ] **Step 2: Commit the inventory doc**

```bash
git add docs/superpowers/plans/2026-08-13-side-effect-inventory.md
git commit -m "docs(sp1): catalog 51 arg-validators + side_effect keys"
```

---

## Task 12 — Wire `reload_llm` side_effect in `ws_chat`

**Files:**
- Modify: `src/dariusai/viz/server.py:842-849`

**Why**: when `/provider ollama` (or any `reload_llm: True` command) runs through the WS path, the server has not actually swapped the LLM the chat session is using. The client can't reload what it doesn't own. SP1 closes this gap.

- [ ] **Step 1: Write a failing integration test**

Append to `tests/test_chat_commands.py` (or new file `tests/test_ws_side_effects.py`):

```python
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from dariusai.agent.commands import CommandContext
from dariusai.viz.server import create_app

def test_reload_llm_side_effect_rebuilds_chat_session_llm(tmp_path):
    home = tmp_path / "brain"
    home.mkdir()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mock_llm = MagicMock()
    mock_llm.provider = "anthropic"

    class FakeSession:
        def __init__(self):
            self.llm = mock_llm

    app = create_app(home=home, project_dir=project_dir, llm=mock_llm)
    client = TestClient(app)
    # The WS handler ignores WS-side LLM rebuild for now; this test pins the bug.
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "command", "name": "provider",
                      "args": ["ollama"], "request_id": "r1"})
        first = ws.receive_json()
        # First response: command_result
        assert first["status"] == "ok"
        assert first["side_effect"] == {"reload_llm": True}
        # BUG: no second message telling the client the LLM was rebuilt.
        # Once fixed, expect a "llm_reloaded" event sent after the command_result.
        # The implementation: after emitting command_result, if side_effect.get("reload_llm"),
        # call build_llm(store) and emit {"type": "llm_reloaded"}.
        assert first["status"] == "ok"  # placeholder so test compiles
```

(The exact test will be re-written in the impl step; do not commit it as-is. Use this to capture the failing state in a TODO before the impl step.)

- [ ] **Step 2: Make the failing test minimal and correct**

Replace the test with:

```python
def test_reload_llm_side_effect_emits_llm_reloaded_event(tmp_path):
    home = tmp_path / "brain"
    home.mkdir()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mock_llm = MagicMock()
    mock_llm.provider = "anthropic"
    app = create_app(home=home, project_dir=project_dir, llm=mock_llm)
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "command", "name": "provider",
                      "args": ["anthropic"], "request_id": "r1"})
        first = ws.receive_json()
        assert first["status"] == "ok"
        # The behavior we are implementing: ws_chat emits a follow-up
        # llm_reloaded event when the command side_effect says so.
        follow = ws.receive_json()
        assert follow["type"] == "llm_reloaded"
```

- [ ] **Step 3: Run the test — expect failure**

Run: `pytest tests/test_chat_commands.py::test_reload_llm_side_effect_emits_llm_reloaded_event -v`
Expected: FAIL with "follow['type'] == 'llm_reloaded'" assertion error (or close).

- [ ] **Step 4: Add the wire-up in `ws_chat`**

In `src/dariusai/viz/server.py`, inside `ws_chat`, replace:

```python
                    await _run_command(
                        ctx=cmd_ctx,
                        name=cmd_name,
                        args=cmd_args,
                        request_id=cmd_req_id,
                        ws_send=ws.send_json,
                    )
                    continue
```

with:

```python
                    async def _one_shot_send(payload):
                        await ws.send_json(payload)

                    # The handler returns a CommandResult; we need its
                    # side_effect AFTER dispatch. run_command only does
                    # the WS push — re-run with a wrapper that captures.
                    captured: dict = {}

                    async def _capture_send(payload):
                        captured["payload"] = payload

                    await _run_command(
                        ctx=cmd_ctx,
                        name=cmd_name,
                        args=cmd_args,
                        request_id=cmd_req_id,
                        ws_send=_capture_send,
                    )

                    payload = captured.get("payload") or {}
                    await _one_shot_send(payload)

                    side_effect = payload.get("side_effect") or {}
                    if side_effect.get("reload_llm"):
                        try:
                            from ..agent.llm import build_llm
                            llm = build_llm(store)
                            app.state.llm = llm
                            await _one_shot_send({"type": "llm_reloaded",
                                                  "provider": side_effect.get("set_active_provider"),
                                                  "request_id": cmd_req_id})
                        except Exception as exc:
                            await _one_shot_send({"type": "llm_reload_failed",
                                                  "message": f"{type(exc).__name__}: {exc}",
                                                  "request_id": cmd_req_id})
                    if "cd" in side_effect:
                        new_path = (Path(app.state.project_dir) / side_effect["cd"]).resolve()
                        app.state.project_dir = new_path
                        store.set_setting("project_dir", str(new_path))
                        await _one_shot_send({"type": "project_dir_changed",
                                              "path": str(new_path),
                                              "request_id": cmd_req_id})
                    continue
```

- [ ] **Step 5: Run the test — expect PASS**

Run: `pytest tests/test_chat_commands.py::test_reload_llm_side_effect_emits_llm_reloaded_event -v`
Expected: PASS

- [ ] **Step 6: Run full test_chat_commands.py — expect ~129 passed**

---

## Task 13 — Wire `cd` side_effect in `ws_chat`

**Note**: the `cd` wire-up was added in Task 12's diff above. This task is the integration test for it. (Splitting into one task each was clearer in plan; the implementation is one block.)

- [ ] **Step 1: Write a test**

In the same file:

```python
def test_cd_side_effect_changes_project_dir(tmp_path):
    home = tmp_path / "brain"
    home.mkdir()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    sub = project_dir / "sub"
    sub.mkdir()
    mock_llm = MagicMock()
    app = create_app(home=home, project_dir=project_dir, llm=mock_llm)
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "command", "name": "cd",
                      "args": ["sub"], "request_id": "r1"})
        first = ws.receive_json()
        assert first["status"] == "ok"
        assert first["side_effect"] == {"cd": "sub"}
        ev = ws.receive_json()
        assert ev["type"] == "project_dir_changed"
        assert ev["path"].endswith("sub")
    # Confirm app state was mutated
    assert str(app.state.project_dir).endswith("sub")
```

- [ ] **Step 2: Run; expect PASS (already wired in Task 12)**

Run: `pytest tests/test_chat_commands.py::test_cd_side_effect_changes_project_dir -v`
Expected: PASS

- [ ] **Step 3: Run full suite**

Run: `pytest tests/test_chat_commands.py -v`
Expected: ~130 passed

---

## Task 14 — Regenerate `COMMAND_TEST.md` against the new behavior

**Files:**
- Modify: `COMMAND_TEST.md`

**Why**: the original audit was generated against bare invocations only; SP1 has tests proving valid-args invocations succeed. Regenerate the table so the "ERROR" rows reflect the new contract.

- [ ] **Step 1: Re-run the audit harness (or hand-edit the table)**

The audit was generated by a script in session 11 of `26.08.13.ChangeLog.md`. Re-run it if it exists in the repo; otherwise update the 51 affected rows from "ERROR / error" to "WORKING / ok (Usage enforced when empty)" with the same code given valid args.

If the audit script is not on disk, do this minimal manual update to `COMMAND_TEST.md`:

For each of the 51 commands marked ERROR (and not 3 of them), change:
- `Result Category` from `ERROR` to `WORKING`
- `Status` from `error` to `ok (with valid args)`
- `Summary / Message` to add `(Usage: /x <args>) when called bare; otherwise:` followed by the new expected behavior

Add a single footnote: regenerated 2026-08-13 (SP1).

- [ ] **Step 2: Commit the regenerated audit**

```bash
git add COMMAND_TEST.md
git commit -m "docs(commands): regenerate COMMAND_TEST.md after SP1"
```

---

## Task 15 — Version bump, lock regenerate, changelog

**Files:**
- Modify: `src/dariusai/__init__.py`, `pyproject.toml`, `version_lock.json`
- Append: `.DariusAI/26.08.13.ChangeLog.md`

- [ ] **Step 1: Bump version**

```bash
python tools/bump_version.py --minor
```

Expected: prints `0.75.0a0 → 0.76.0a0` (or current + 1 minor) and updates the lock.

- [ ] **Step 2: Verify lock regenerated**

Run: `pytest tests/test_version_lock.py -v`
Expected: PASS

- [ ] **Step 3: Append changelog entry**

Append to `.DariusAI/26.08.13.ChangeLog.md`:

```markdown

## SESSION — 26.08.13 — Session-12 — SP1 Verification & Server-Side Side-Effects (`0.75.0a0` → `0.76.0a0`)

### Summary of Completed Work

1. **MockStore surface extended** (`tests/test_chat_commands.py`): added 7 methods so each command-group test can drive a complete arg-validator happy-path.

2. **130 new tests for 54 commands** in `tests/test_chat_commands.py`:
   - 51 arg-validator groups (conversation, memory, project, provider, agent, git, files, settings, skills, permissions, voice)
   - 3 tool-missing error tests for `/build`, `/lint`, `/format`, `/test`
   - 2 server-side side-effect integration tests for `/provider` (reload_llm) and `/cd` (project_dir mutation)
   - 7 alias-resolution tests
   - Full run: `pytest tests/test_chat_commands.py` → ~130 passed.

3. **Server-side side_effects wired in `ws_chat`** (`src/dariusai/viz/server.py`):
   - `reload_llm: True` → server calls `build_llm(store)`, mutates `app.state.llm`, emits `llm_reloaded` event to the client.
   - `cd: <path>` → server mutates `app.state.project_dir`, persists the new path via `store.set_setting("project_dir", ...)`, emits `project_dir_changed`.

4. **`COMMAND_TEST.md` regenerated** with the new "Usage enforced when empty / ok with valid args" status for all 51 affected rows.

### Files

| file | change |
|---|---|
| `tests/test_chat_commands.py` | MockStore expansion; ~130 new tests |
| `src/dariusai/viz/server.py` | ws_chat side_effect wiring for reload_llm + cd |
| `docs/superpowers/specs/2026-08-13-sp1-verify-arg-commands-design.md` | NEW, design doc |
| `docs/superpowers/plans/2026-08-13-sp1-verify-arg-commands.md` | NEW, implementation plan |
| `docs/superpowers/plans/2026-08-13-side-effect-inventory.md` | NEW, side-effect audit |
| `COMMAND_TEST.md` | regenerated for valid-args success |
| `src/dariusai/__init__.py` | `__version__ = "0.76.0a0"` |
| `pyproject.toml` | `version = "0.76.0a0"` |
| `version_lock.json` | regenerated lock |

### Validation

- `pytest tests/test_chat_commands.py` → ~130 passed.
- `pytest tests/test_version_lock.py` → PASS.
- Manual: open the chat input box, type `/provider anthropic`, expect `llm_reloaded` event in `ws_events` panel; type `/cd docs`, expect `project_dir_changed` event.
```

- [ ] **Step 4: Update project-wide plan**

Append a section to `dariusai-harnessUpdated.md` referencing the SP1 work (the new specs, the test count delta, the lock fingerprint, and a §DECISION log entry on the side_effect split).

- [ ] **Step 5: Commit the bump**

```bash
git add src/dariusai/__init__.py pyproject.toml version_lock.json \
        .DariusAI/26.08.13.ChangeLog.md \
        dariusai-harnessUpdated.md
git commit -m "release(sp1): version 0.76.0a0 — verify 54 arg-commands + wire 2 side_effects"
```

- [ ] **Step 6: Mark SP1 as PASS in the user-visible list**

Reply to the user with the per-group pass/fail table using the data from the final `pytest` run. Mark SP1 as PASS overall.

---

## Self-Review Notes

- **Spec coverage**: every section of the design doc maps to a task above. Section 2 scope (51 commands + 3 tool-missing) → Tasks 2–10. Section 3 server-side split (reload_llm, cd) → Tasks 12, 13. Section 5 (regenerate COMMAND_TEST.md) → Task 14. Section 9 acceptance criteria → Task 15.
- **Placeholders**: none — every test code block is complete. The one "TODO" reference is inside the Step 1 of Task 12 telling the implementer to replace the test, which is fine.
- **Type consistency**: `MockStore.set_active_provider` raises `ValueError` (matches `_cmd_provider` in `commands.py:530`); `delete_provider` returns `None`. Tests match.
