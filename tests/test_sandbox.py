"""The sandbox boundary: confinement, process-tree kill, credential scrubbing.

These are the guarantees the agent's autonomy rests on, so they're tested
against the real filesystem and real subprocesses rather than mocks.
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent.sandbox import Sandbox, SandboxViolation
from dariusai.agent.tools import build_tool_registry
from dariusai.brain.store import BrainStore


# ---- confinement ----------------------------------------------------------

def test_relative_paths_resolve_inside_the_root(tmp_path):
    sb = Sandbox(root=tmp_path)
    assert sb.resolve("sub/file.txt") == (tmp_path / "sub/file.txt").resolve()


def test_absolute_paths_inside_the_root_are_allowed(tmp_path):
    """The agent legitimately uses absolute paths — inside is inside."""
    sb = Sandbox(root=tmp_path)
    inside = tmp_path / "a" / "b.txt"
    assert sb.resolve(str(inside)) == inside.resolve()


@pytest.mark.parametrize("escape", ["..", "../outside.txt", "sub/../../outside.txt"])
def test_dotdot_traversal_is_refused(tmp_path, escape):
    sb = Sandbox(root=tmp_path / "project")
    (tmp_path / "project").mkdir()
    with pytest.raises(SandboxViolation):
        sb.resolve(escape)


def test_absolute_path_outside_the_root_is_refused(tmp_path):
    sb = Sandbox(root=tmp_path / "project")
    (tmp_path / "project").mkdir()
    with pytest.raises(SandboxViolation):
        sb.resolve(str(Path.home() / ".dariusai" / "brain.db"))


def test_symlink_out_of_the_root_is_refused(tmp_path):
    """Resolution happens before the containment check, so a symlink can't
    be used as a door."""
    project = tmp_path / "project"
    project.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    link = project / "door"
    try:
        link.symlink_to(secret, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this machine")

    with pytest.raises(SandboxViolation):
        Sandbox(root=project).resolve("door/leak.txt")


def test_unrestricted_is_explicit_and_lets_anything_through(tmp_path):
    assert Sandbox.unrestricted().confined is False
    assert Sandbox.unrestricted().resolve("C:/anywhere") == Path("C:/anywhere")


# ---- the tools actually enforce it ----------------------------------------

def test_agent_cannot_read_outside_its_sandbox(tmp_path):
    outside = tmp_path / "keys.txt"
    outside.write_text("sk-ant-secret", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()

    reg = build_tool_registry(BrainStore(tmp_path / "brain"), Sandbox(root=project))
    result = reg.call("read_file", {"path": str(outside)})

    assert "sk-ant-secret" not in result
    assert "escapes the sandbox" in result


def test_agent_cannot_write_outside_its_sandbox(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    target = tmp_path / "evil.txt"

    reg = build_tool_registry(BrainStore(tmp_path / "brain"), Sandbox(root=project))
    result = reg.call("write_file", {"path": str(target), "content": "x"})

    assert not target.exists()
    assert "escapes the sandbox" in result


def test_agent_works_normally_inside_the_sandbox(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reg = build_tool_registry(BrainStore(tmp_path / "brain"), Sandbox(root=project))

    assert "wrote" in reg.call("write_file", {"path": "notes.txt", "content": "hello"})
    assert reg.call("read_file", {"path": "notes.txt"}) == "hello"
    assert "notes.txt" in reg.call("list_dir", {"path": "."})


# ---- execution ------------------------------------------------------------

def test_shell_runs_in_the_sandbox_root_by_default(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    result = Sandbox(root=project).run(f'"{sys.executable}" -c "import os;print(os.getcwd())"')
    assert result.returncode == 0
    assert str(project.resolve()).lower() in result.output.lower()


def test_shell_cannot_be_pointed_outside_the_sandbox(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(SandboxViolation):
        Sandbox(root=project).run("echo hi", cwd=str(tmp_path))


def test_secrets_are_stripped_from_the_child_environment(tmp_path, monkeypatch):
    """A build script or postinstall hook has no business seeing the keys
    that drive the agent."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-leak")
    monkeypatch.setenv("SOME_SECRET", "nope")
    monkeypatch.setenv("PATH_LIKE_NORMAL_VAR", "kept")

    sb = Sandbox(root=tmp_path)
    env = sb.environment()
    assert "ANTHROPIC_API_KEY" not in env
    assert "SOME_SECRET" not in env
    assert env.get("PATH_LIKE_NORMAL_VAR") == "kept"

    out = sb.run(f'"{sys.executable}" -c "import os;print(os.environ.get(chr(65)+\'NTHROPIC_API_KEY\'))"').output
    assert "sk-ant-must-not-leak" not in out


def test_timeout_reports_itself(tmp_path):
    result = Sandbox(root=tmp_path, timeout=1).run(f'"{sys.executable}" -c "import time;time.sleep(30)"')
    assert result.timed_out is True
    assert "TIMED OUT" in result.to_text()


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows-only")
def test_timeout_kills_the_whole_process_tree(tmp_path):
    """The reason the job object exists: killing the launched shell leaves
    its children running. A grandchild that outlives the timeout is a leak,
    and this project has already shipped that bug once."""
    marker = tmp_path / "grandchild_alive.txt"
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys, time\n"
        f"grand = r'''{tmp_path / 'grand.py'}'''\n"
        "subprocess.Popen([sys.executable, grand])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    (tmp_path / "grand.py").write_text(
        "import time\n"
        f"open(r'''{marker}''', 'w').write('started')\n"
        "time.sleep(20)\n"
        f"open(r'''{marker}''', 'a').write(' survived')\n",
        encoding="utf-8",
    )

    result = Sandbox(root=tmp_path, timeout=3).run(f'"{sys.executable}" "{child}"')
    assert result.timed_out is True

    time.sleep(2)
    assert marker.exists(), "grandchild never started — test is not exercising what it claims"
    assert "survived" not in marker.read_text(), "grandchild outlived the timeout: the tree was not killed"


def test_output_is_truncated_not_unbounded(tmp_path):
    result = Sandbox(root=tmp_path).run(f'"{sys.executable}" -c "print(\'x\' * 100000)"')
    assert len(result.to_text(limit=5000)) < 6000
    assert "truncated" in result.to_text(limit=5000)
