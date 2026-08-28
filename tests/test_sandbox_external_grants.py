"""External-read grants: the one-turn, read-only, subtree-only window into
directories outside the sandbox root.

The rules under test:

* the grant must be a directory, not an ancestor of the root, and not a
  parent of an already-granted path (widening is refused);
* an approved grant makes the whole subtree readable but never writable;
* destructive shell commands are refused inside a granted tree even for
  reads (the tree is a read-only reference);
* grants are cleared at the top of each user turn — the "one grant per
  prompt" contract lives at the ChatSession boundary.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent.chat import ChatSession
from dariusai.agent.sandbox import (
    PermissionBroker,
    Sandbox,
    SandboxViolation,
)
from dariusai.agent.tools import build_tool_registry
from dariusai.brain.store import BrainStore


class AlwaysAllow(PermissionBroker):
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def request(self, path: str, reason: str) -> bool:
        self.calls.append((path, reason))
        return True


class AlwaysDeny(PermissionBroker):
    def request(self, path: str, reason: str) -> bool:
        return False


# ---- grant_external validation --------------------------------------------

def test_grant_must_be_an_existing_directory(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    sb = Sandbox(root=project)
    with pytest.raises(SandboxViolation):
        sb.grant_external(tmp_path / "does-not-exist")
    file_target = tmp_path / "file.txt"
    file_target.write_text("hi", encoding="utf-8")
    with pytest.raises(SandboxViolation):
        sb.grant_external(file_target)


def test_grant_refuses_ancestor_of_the_sandbox_root(tmp_path):
    """`C:\\MyCreations\\CastleHellDefenders\\RustEngine` is fine, but the
    parent `C:\\MyCreations\\CastleHellDefenders` is not — that would let
    the agent read the workspace's own siblings through a side door."""
    project = tmp_path / "workbench" / "current-project"
    project.mkdir(parents=True)
    sb = Sandbox(root=project)
    # Direct parent
    with pytest.raises(SandboxViolation):
        sb.grant_external(project.parent)
    # Any ancestor
    with pytest.raises(SandboxViolation):
        sb.grant_external(tmp_path)


def test_grant_refuses_widening_an_existing_grant(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference" / "RustEngine"
    reference.mkdir(parents=True)

    sb = Sandbox(root=project)
    sb.grant_external(reference)
    # A grant on the parent would widen the read window — refused.
    with pytest.raises(SandboxViolation):
        sb.grant_external(reference.parent)


def test_grant_is_idempotent(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()
    sb = Sandbox(root=project)
    sb.grant_external(reference)
    sb.grant_external(reference)
    assert sb.external_grants == [reference.resolve()]


# ---- resolve() with grants -------------------------------------------------

def test_descendant_of_grant_is_readable(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference" / "RustEngine"
    (reference / "src").mkdir(parents=True)
    inside = reference / "src" / "main.rs"
    inside.write_text("fn main() {}", encoding="utf-8")

    sb = Sandbox(root=project)
    sb.grant_external(reference)

    resolved = sb.resolve(str(inside))
    assert resolved == inside.resolve()


def test_write_into_grant_is_refused_even_when_granted(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()

    sb = Sandbox(root=project)
    sb.grant_external(reference)

    # Reads: fine.
    sb.resolve(str(reference / "any-file.txt"), for_write=False)
    # Writes: refused.
    with pytest.raises(SandboxViolation):
        sb.resolve(str(reference / "any-file.txt"), for_write=True)


def test_path_outside_root_and_outside_grant_is_still_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()
    other = tmp_path / "somewhere-else"
    other.mkdir()

    sb = Sandbox(root=project)
    sb.grant_external(reference)

    with pytest.raises(SandboxViolation):
        sb.resolve(str(other / "file.txt"))


# ---- destructive-command scanner ------------------------------------------

@pytest.mark.parametrize("command", [
    "rm -rf src",
    "del main.rs",
    "mv src dst",
    "git reset --hard",
    "git clean -fd",
    "cargo clean",
])
def test_destructive_shell_refused_when_cwd_is_in_grant(tmp_path, command):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()

    sb = Sandbox(root=project)
    sb.grant_external(reference)

    with pytest.raises(SandboxViolation):
        sb.run(command, cwd=str(reference))


def test_non_destructive_command_still_runs_in_grant(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()

    sb = Sandbox(root=project, timeout=10)
    sb.grant_external(reference)

    result = sb.run(f'"{sys.executable}" -c "print(\'ok\')"', cwd=str(reference))
    assert result.returncode == 0
    assert "ok" in result.output


def test_destructive_command_refused_when_command_mentions_grant_path(tmp_path):
    """Even with cwd inside the sandbox root, `rm <grant-path>` still gets
    caught — otherwise the read-only guarantee would leak via absolute paths."""
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "file.txt").write_text("keep me", encoding="utf-8")

    sb = Sandbox(root=project)
    sb.grant_external(reference)

    with pytest.raises(SandboxViolation):
        sb.run(f"rm {reference / 'file.txt'}", cwd=str(project))


# ---- request_and_grant end-to-end -----------------------------------------

def test_request_and_grant_asks_broker_and_records(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()

    broker = AlwaysAllow()
    sb = Sandbox(root=project, broker=broker)
    ok, message = sb.request_and_grant(str(reference), "want to read the engine")

    assert ok is True
    assert reference.resolve() in sb.external_grants
    assert broker.calls == [(str(reference.resolve()), "want to read the engine")]
    assert "granted read-only access" in message


def test_request_denied_leaves_grants_untouched(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()

    sb = Sandbox(root=project, broker=AlwaysDeny())
    ok, message = sb.request_and_grant(str(reference), "please?")

    assert ok is False
    assert sb.external_grants == []
    assert "denied" in message


def test_request_pre_validates_before_prompting(tmp_path):
    """A grant that couldn't be added anyway (ancestor of the root) must
    not reach the human — no point prompting for something we'd reject."""
    project = tmp_path / "workbench" / "current"
    project.mkdir(parents=True)

    broker = AlwaysAllow()
    sb = Sandbox(root=project, broker=broker)
    ok, message = sb.request_and_grant(str(project.parent), "widen me")

    assert ok is False
    assert broker.calls == []
    assert "ancestor" in message


def test_default_broker_denies(tmp_path):
    """No broker wired in ⇒ every request denied — grants are opt-in per surface."""
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()

    sb = Sandbox(root=project)  # no broker
    ok, _ = sb.request_and_grant(str(reference), "no ui here")
    assert ok is False


# ---- ChatSession clears grants on new turn --------------------------------

class _StubLLM:
    """One-shot LLM that returns an empty assistant reply, ending the turn."""

    def complete(self, system, messages, tools):
        return {"content": [{"type": "text", "text": "ok"}], "usage": {}, "context_window": 100_000}


def test_chat_session_clears_grants_at_top_of_each_turn(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()

    sandbox = Sandbox(root=project, broker=AlwaysAllow())
    sandbox.grant_external(reference)
    assert sandbox.external_grants  # sanity: granted before the turn

    tools = build_tool_registry(BrainStore(tmp_path / "brain"), sandbox)
    session = ChatSession(llm=_StubLLM(), tools=tools)
    session.send("next prompt, please forget what you had")

    # The grant from before the send() call must be gone — the whole point
    # of the "one grant per user turn" contract is that the agent re-asks.
    assert sandbox.external_grants == []


# ---- tool wiring -----------------------------------------------------------

def test_request_external_read_tool_grants_on_approval(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "readme.md").write_text("engine notes", encoding="utf-8")

    sandbox = Sandbox(root=project, broker=AlwaysAllow())
    reg = build_tool_registry(BrainStore(tmp_path / "brain"), sandbox)

    approve = reg.call("request_external_read", {
        "path": str(reference), "reason": "study the engine",
    })
    assert "granted" in approve

    # Now read_file reaches inside the grant.
    body = reg.call("read_file", {"path": str(reference / "readme.md")})
    assert "engine notes" in body


def test_write_file_refuses_grant_path_even_after_approval(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reference = tmp_path / "reference"
    reference.mkdir()

    sandbox = Sandbox(root=project, broker=AlwaysAllow())
    reg = build_tool_registry(BrainStore(tmp_path / "brain"), sandbox)
    reg.call("request_external_read", {"path": str(reference), "reason": "look"})

    result = reg.call("write_file", {
        "path": str(reference / "leak.txt"), "content": "no",
    })
    assert "escapes the sandbox" in result
    assert not (reference / "leak.txt").exists()
