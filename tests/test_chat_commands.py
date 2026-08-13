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
