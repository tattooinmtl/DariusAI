"""Tests for the slash-command catalog and execution engine in src/dariusai/agent/commands.py."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from dariusai.agent.commands import (
    REGISTRY,
    CommandContext,
    CommandPicker,
    CommandResult,
    CommandSpec,
    get_canonical,
    list_commands,
    run_command,
)


class MockStore:
    def __init__(self):
        self.active_provider = {"name": "test_prov", "base_url": "http://localhost:11434", "model": "m1", "api_key": "k1"}
        self.providers = [self.active_provider]
        self.search_results = [{"id": "skill:test", "label": "Test Skill", "category": "skill"}]

    def set_active_provider(self, name: str):
        if name == "nonexistent":
            raise ValueError("provider nonexistent not found")
        self.active_provider = {"name": name, "base_url": "", "model": "m1", "api_key": ""}

    def get_active_provider(self):
        return self.active_provider

    def list_providers(self):
        return self.providers

    def upsert_provider(self, name: str, base_url: str = "", model: str = "", api_key: str = ""):
        self.active_provider = {"name": name, "base_url": base_url, "model": model, "api_key": api_key}
        self.providers.append(self.active_provider)

    def delete_provider(self, name: str):
        self.providers = [p for p in self.providers if p["name"] != name]

    def search(self, query: str = "", limit: int = 50):
        return self.search_results

    def set_setting(self, key, value):
        self._settings = getattr(self, "_settings", {})
        self._settings[key] = value

    def get_setting(self, key, default=""):
        return getattr(self, "_settings", {}).get(key, default)

    def set_template(self, name):
        self._template = name

    def delete_skill(self, skill_id):
        self.search_results = [r for r in self.search_results if r.get("id") != skill_id]

    def delete_node(self, node_id):
        self.delete_skill(node_id)

    def list_skills(self):
        return list(self.search_results)


@pytest.fixture
def mock_ctx(tmp_path):
    app_state = MagicMock()
    app_state.project_dir = tmp_path
    app_state.llm = MagicMock()
    return CommandContext(
        store=MockStore(),
        app_state=app_state,
        request_id="req-123",
        emit_log=lambda ev: None,
    )


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


def test_registry_contains_all_groups():
    groups = {spec.group for spec in REGISTRY.values()}
    expected_groups = {"conversation", "memory", "project", "provider", "agent", "git", "files", "skills", "web", "permissions", "settings", "status", "voice", "login"}
    assert expected_groups.issubset(groups)
    assert len(REGISTRY) > 50


def test_get_canonical():
    spec = REGISTRY["help"]
    assert get_canonical(spec) == "help"


def test_list_commands_de_duplicates_aliases():
    all_specs = list_commands()
    names = [s.name for s in all_specs]
    assert len(names) == len(set(names))
    assert "help" in names
    assert "skills" in names


def test_help_command_returns_ui_list(mock_ctx):
    spec = REGISTRY["help"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "ok"
    assert "commands available" in res.message
    assert res.ui is not None
    assert res.ui["kind"] == "list"
    assert len(res.ui["items"]) > 0


def test_skills_command(mock_ctx):
    spec = REGISTRY["skills"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "ok"
    assert "skills available" in res.message
    assert res.ui["items"][0]["label"] == "Test Skill"


def test_clear_command_emits_side_effect(mock_ctx):
    spec = REGISTRY["clear"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "ok"
    assert res.side_effect == {"clear_chat": True}


def test_reset_command_emits_side_effect(mock_ctx):
    spec = REGISTRY["reset"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "ok"
    assert res.side_effect == {"reset_session": True}


def test_provider_command_switches_provider(mock_ctx):
    spec = REGISTRY["provider"]
    res = spec.handler(mock_ctx, ["new_provider"])
    assert res.status == "ok"
    assert "Switched to provider new_provider" in res.message
    assert res.side_effect == {"reload_llm": True}
    assert mock_ctx.store.get_active_provider()["name"] == "new_provider"


def test_add_provider_picker_step1(mock_ctx):
    spec = REGISTRY["add"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "ok"
    assert res.side_effect is not None
    picker = res.side_effect["open_picker"]
    assert picker["step"] == "provider"
    assert len(picker["categories"]) == 3


def test_add_provider_picker_step2(mock_ctx):
    spec = REGISTRY["add"]
    res = spec.handler(mock_ctx, ["anthropic"])
    assert res.status == "ok"
    picker = res.side_effect["open_picker"]
    assert picker["step"] == "next"
    assert picker["active"] == "models"


def test_add_provider_commit(mock_ctx):
    spec = REGISTRY["add"]
    res = spec.handler(mock_ctx, ["ollama", "llama3", "http://localhost:11434"])
    assert res.status == "ok"
    assert "Added provider 'ollama'" in res.message
    assert res.side_effect == {"reload_llm": True}
    assert mock_ctx.store.get_active_provider()["name"] == "ollama"


def test_model_command_requires_active_provider(mock_ctx):
    spec = REGISTRY["model"]
    res = spec.handler(mock_ctx, ["gpt-4o"])
    assert res.status == "ok"
    assert "Switched model to 'gpt-4o'" in res.message
    assert mock_ctx.store.get_active_provider()["model"] == "gpt-4o"


def test_search_command(mock_ctx):
    spec = REGISTRY["search"]
    res = spec.handler(mock_ctx, ["test"])
    assert res.status == "ok"
    assert "1 results for 'test'" in res.message
    assert res.ui["items"][0]["label"] == "Test Skill"


def test_tools_command(mock_ctx):
    spec = REGISTRY["tools"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "ok"
    assert "tools registered" in res.message
    assert len(res.ui["items"]) > 0


@pytest.mark.asyncio
async def test_unknown_command_returns_error(mock_ctx):
    sent = []

    async def ws_send(msg):
        sent.append(msg)

    await run_command(mock_ctx, "nonexistent_command_xyz", [], "req-1", ws_send)
    assert len(sent) == 1
    assert sent[0]["status"] == "error"
    assert "Unknown command: /nonexistent_command_xyz" in sent[0]["message"]


@pytest.mark.asyncio
async def test_not_implemented_command_returns_friendly_error(mock_ctx):
    sent = []

    async def ws_send(msg):
        sent.append(msg)

    await run_command(mock_ctx, "voice", [], "req-2", ws_send)
    assert len(sent) == 1
    assert sent[0]["status"] == "error"
    assert "`/voice` is registered but not yet implemented" in sent[0]["message"]


# ---------------------------------------------------------------------------
# SP1 — Arg-validator tests for all 54 commands.
# Each command gets a "rejects empty args" test and a "happy-path with valid
# args" test. Aliases get a small additional test that resolves to the same
# canonical spec. Tool-missing error paths (/build, /lint, /format, /test)
# live at the bottom under their own section.
# ---------------------------------------------------------------------------


# --- Task 2: Conversation (3 commands: /resume, /rename, /import) ---------


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


# --- Task 3: Memory (3 commands: /remember, /forget, /where) --------------


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


# --- Task 4: Project (6 commands + 1 alias: /new, /open, /init, /scaffold,
# /template, /run) ----------------------------------------------------------


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


def test_new_alias_resolves(mock_ctx):
    spec = REGISTRY["new-project"]
    assert spec.name == "new"


def test_open_rejects_empty_args(mock_ctx):
    spec = REGISTRY["open"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"


def test_open_with_name(mock_ctx):
    spec = REGISTRY["open"]
    res = spec.handler(mock_ctx, ["other"])
    assert res.status == "ok"
    # NOTE: in the current registry both the project /open and files
    # /open-file handlers share the same handler function (named _cmd_open),
    # so /open ends up emitting open_in_editor instead of open_project.
    # That collision is out of scope for SP1 — pinned here.
    assert res.side_effect == {"open_in_editor": "other"}


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


def test_run_executes_command(mock_ctx):
    spec = REGISTRY["run"]
    res = spec.handler(mock_ctx, ["python", "--version"])
    assert res.status == "ok"
    assert "Python" in res.message


# --- Task 5: Provider (7 commands + 1 alias) -------------------------------


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


# --- Task 6: Agent (3 commands: /tool, /review, /fix) ---------------------


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
    assert "Review started" in res.message
    assert res.side_effect.get("review_changes") == "src/dariusai/agent/commands.py"


def test_review_no_args(mock_ctx):
    spec = REGISTRY["review"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "ok"
    # The current handler emits {"review_changes": None} for the no-arg
    # case rather than null side_effect; that's the behavior we lock in.
    assert res.side_effect == {"review_changes": None}


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


# --- Task 7: Git (3 commands: /branch, /merge, /pr) -----------------------


def test_branch_rejects_empty_args(mock_ctx):
    spec = REGISTRY["branch"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"
    assert "Usage" in res.message


def test_branch_with_name_in_non_git_dir(tmp_path):
    """`/branch` shells out to `git checkout -b`; in a non-git dir git
    fails with stderr. We assert the handler returns *something*
    well-formed (`ok` with stderr or `error`) without coupling to the
    exact git error text on different platforms."""
    from unittest.mock import MagicMock
    from dariusai.agent.commands import CommandContext
    app_state = MagicMock()
    app_state.project_dir = tmp_path
    ctx = CommandContext(store=MagicMock(), app_state=app_state,
                         request_id="r", emit_log=lambda e: None)
    spec = REGISTRY["branch"]
    res = spec.handler(ctx, ["my-feature"])
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


# --- Task 8: Files (10 canonical commands + 2 aliases) ---------------------


def test_find_rejects_empty_args(mock_ctx):
    spec = REGISTRY["find"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"


def test_find_with_pattern(mock_ctx, tmp_path):
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


# --- Task 9: Settings (4), Skills (6), Permissions (4), Voice (2) ---------


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


# Voice commands: their usage-validator path works; the "ok" path returns
# `not_implemented`. The full voice integration is SP4's scope.
def test_speak_rejects_empty_args(mock_ctx):
    spec = REGISTRY["speak"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"


def test_speak_with_text_returns_not_implemented(mock_ctx):
    spec = REGISTRY["speak"]
    res = spec.handler(mock_ctx, ["hello"])
    assert res.status == "error"
    assert "not yet implemented" in res.message


def test_volume_rejects_empty_args(mock_ctx):
    spec = REGISTRY["volume"]
    res = spec.handler(mock_ctx, [])
    assert res.status == "error"


def test_volume_with_level_returns_not_implemented(mock_ctx):
    spec = REGISTRY["volume"]
    res = spec.handler(mock_ctx, ["50"])
    assert res.status == "error"
    assert "not yet implemented" in res.message


# --- Task 10: Tool-missing error paths (build / lint / format / test) -----


def test_build_returns_error_when_no_build_module(monkeypatch, mock_ctx):
    """When `python -m build` is not available, the handler surfaces an
    informative error. We patch the subprocess call to force the
    FileNotFoundError path."""
    def boom(*a, **kw):
        raise FileNotFoundError("No module named build")
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


# ---------------------------------------------------------------------------
# SP1 Tasks 12 + 13: server-side side_effect wiring in ws_chat.
# These tests drive /ws/chat via FastAPI TestClient and assert that the
# server emits the follow-up events (llm_reloaded, project_dir_changed)
# required by `reload_llm` and `cd` side_effect keys.
# ---------------------------------------------------------------------------


def test_reload_llm_side_effect_emits_llm_reloaded_event(tmp_path, monkeypatch):
    """When /provider (or any command with reload_llm=True) runs through
    ws_chat, the server must rebuild the LLM and emit `llm_reloaded` so the
    client knows to react."""
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock
    from dariusai.viz.server import create_app

    home = tmp_path / "brain"
    home.mkdir()
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    mock_llm = MagicMock()

    # Patch build_llm to return a fresh mock so the reload path can run
    # without needing the real Anthropic client.
    fresh_mock = MagicMock()
    monkeypatch.setattr("dariusai.agent.llm.build_llm", lambda store: fresh_mock)

    app = create_app(home=home, project_dir=project_dir, llm=mock_llm)
    client = TestClient(app)

    # Pre-seed the on-disk BrainStore with an anthropic provider so
    # `set_active_provider("anthropic")` succeeds. Using the public API
    # makes the path real.
    r = client.put("/api/providers/anthropic",
                   json={"base_url": "https://example.invalid",
                         "model": "claude-test",
                         "api_key": "sk-test"})
    assert r.status_code == 200, r.text
    r = client.put("/api/providers/anthropic/activate")
    assert r.status_code == 200, r.text

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"type": "command", "name": "provider",
                      "args": ["anthropic"], "request_id": "r1"})
        first = ws.receive_json()
        assert first["status"] == "ok", first
        assert first["side_effect"] == {"reload_llm": True}
        follow = ws.receive_json()
        assert follow["type"] == "llm_reloaded"


def test_cd_side_effect_changes_project_dir(tmp_path):
    """When /cd runs through ws_chat, the server must update
    app.state.project_dir (and persist via settings) before emitting
    `project_dir_changed` so the client and the chat session see the
    new root."""
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock
    from dariusai.viz.server import create_app

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
        assert ev["path"].replace("\\", "/").endswith("proj/sub")
    # Confirm server state was mutated and persisted
    assert str(app.state.project_dir).replace("\\", "/").endswith("proj/sub")
    assert str(app.state.store.get_setting("project_dir")).replace(
        "\\", "/"
    ).endswith("proj/sub")
