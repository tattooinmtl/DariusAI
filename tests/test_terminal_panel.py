"""The Terminal panel is a real shell, not a mock-up.

These cover the two halves: TerminalSession (spawn, stream, cd, interrupt)
and the /ws/terminal socket that the panel talks to.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from dariusai.viz.server import create_app
from dariusai.viz.terminal import TerminalSession, shell_name


def collect(session, cmd):
    chunks = []
    code = session.run(cmd, chunks.append)
    return code, "".join(chunks)


def test_runs_a_command_and_streams_its_output(tmp_path):
    session = TerminalSession(tmp_path)
    code, out = collect(session, f'"{sys.executable}" -c "print(6*7)"')
    assert code == 0
    assert "42" in out


def test_nonzero_exit_is_reported(tmp_path):
    session = TerminalSession(tmp_path)
    code, _ = collect(session, f'"{sys.executable}" -c "raise SystemExit(3)"')
    assert code == 3


def test_stderr_is_merged_into_the_stream(tmp_path):
    session = TerminalSession(tmp_path)
    code, out = collect(
        session, f'"{sys.executable}" -c "import sys; sys.stderr.write(\'boom\')"'
    )
    assert code == 0
    assert "boom" in out


def test_cd_moves_the_session_and_sticks(tmp_path):
    (tmp_path / "sub").mkdir()
    session = TerminalSession(tmp_path)
    code, _ = collect(session, "cd sub")
    assert code == 0
    assert session.cwd == (tmp_path / "sub").resolve()
    # and the next command actually runs there
    _, out = collect(session, f'"{sys.executable}" -c "import os; print(os.getcwd())"')
    assert "sub" in out


def test_cd_into_nothing_fails_without_moving(tmp_path):
    session = TerminalSession(tmp_path)
    code, out = collect(session, "cd does-not-exist")
    assert code == 1
    assert "no such directory" in out
    assert session.cwd == tmp_path.resolve()


def test_blank_command_is_a_no_op(tmp_path):
    assert collect(TerminalSession(tmp_path), "   ") == (0, "")


def test_stdin_reaches_the_running_process(tmp_path):
    """A command that reads stdin must be answerable from the panel."""
    import threading

    session = TerminalSession(tmp_path)
    chunks: list[str] = []
    script = "import sys; print('got:' + sys.stdin.readline().strip())"
    thread = threading.Thread(
        target=lambda: session.run(f'"{sys.executable}" -c "{script}"', chunks.append)
    )
    thread.start()
    deadline = time.monotonic() + 20
    while not session.running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert session.send_input("hello\n")
    thread.join(timeout=30)
    assert "got:hello" in "".join(chunks)


def test_interrupt_kills_a_long_running_command(tmp_path):
    import threading

    session = TerminalSession(tmp_path)
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(
            session.run(f'"{sys.executable}" -c "import time; time.sleep(60)"', lambda _t: None)
        )
    )
    thread.start()
    deadline = time.monotonic() + 20
    while not session.running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert session.interrupt()
    thread.join(timeout=30)
    assert not thread.is_alive(), "interrupt did not end the command"
    assert result and result[0] != 0
    assert not session.running


def test_version_builtin_reports_the_running_version(tmp_path):
    import dariusai

    session = TerminalSession(tmp_path)
    for spelling in ("--version", "-v", "  --version  "):
        code, out = collect(session, spelling)
        assert code == 0
        assert dariusai.__version__ in out
        assert "DariusAI" in out


def test_info_builtin_lists_the_specs(tmp_path):
    import dariusai

    session = TerminalSession(tmp_path)
    code, out = collect(session, "--info")
    assert code == 0
    assert dariusai.__version__ in out
    for label in ("Version", "Python", "Platform", "Shell", "Directory"):
        assert label in out
    # -i is the same command
    assert collect(session, "-i")[1] == out


def test_info_includes_rows_supplied_by_the_app(tmp_path):
    session = TerminalSession(tmp_path, info=lambda: {"Brain": "somewhere", "Uptime": "3m 1s"})
    _, out = collect(session, "--info")
    assert "Brain" in out and "somewhere" in out
    assert "Uptime" in out and "3m 1s" in out


def test_a_broken_info_provider_does_not_break_the_command(tmp_path):
    """A bad row must not cost the user the version they asked for."""

    def boom():
        raise RuntimeError("store is gone")

    session = TerminalSession(tmp_path, info=boom)
    code, out = collect(session, "--info")
    assert code == 0
    assert "Version" in out
    assert "store is gone" in out


def test_darius_prefix_is_accepted(tmp_path):
    import dariusai

    session = TerminalSession(tmp_path)
    assert dariusai.__version__ in collect(session, "darius --version")[1]
    assert "Python" in collect(session, "darius")[1]  # bare `darius` shows the specs


def test_the_real_cli_is_not_shadowed(tmp_path):
    """`dariusai --version` must reach the actual console script, not the
    built-in — shadowing it would hide the CLI's own behaviour."""
    session = TerminalSession(tmp_path)
    assert session._try_builtin("dariusai --version", lambda _t: None) is None


def test_help_builtin_lists_the_builtins(tmp_path):
    session = TerminalSession(tmp_path)
    code, out = collect(session, "--help")
    assert code == 0
    for expected in ("--info", "--version", "cd ", "clear"):
        assert expected in out


def test_builtins_do_not_swallow_ordinary_commands(tmp_path):
    session = TerminalSession(tmp_path)
    assert session._try_builtin("git status", lambda _t: None) is None
    assert session._try_builtin("python --version", lambda _t: None) is None


def test_websocket_info_reports_live_app_state(tmp_path):
    """Over the socket, --info gains the rows only the server knows."""
    import dariusai

    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    with TestClient(app).websocket_connect("/ws/terminal") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "run", "cmd": "--info"})
        out = ""
        for _ in range(100):
            msg = ws.receive_json()
            if msg["type"] == "out":
                out += msg["data"]
            elif msg["type"] == "exit":
                assert msg["code"] == 0
                break
    assert dariusai.__version__ in out
    for label in ("Brain", "Project", "Provider", "Uptime"):
        assert label in out
    assert str(tmp_path.resolve()) in out


def test_websocket_runs_a_command_end_to_end(tmp_path):
    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    with TestClient(app).websocket_connect("/ws/terminal") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert Path(ready["cwd"]) == tmp_path.resolve()
        assert ready["shell"] == shell_name()

        ws.send_json({"type": "run", "cmd": f'"{sys.executable}" -c "print(\'hi-from-shell\')"'})
        output, code = "", None
        for _ in range(200):
            msg = ws.receive_json()
            if msg["type"] == "out":
                output += msg["data"]
            elif msg["type"] == "exit":
                code = msg["code"]
                break
        assert code == 0
        assert "hi-from-shell" in output


def test_websocket_cd_updates_the_reported_cwd(tmp_path):
    (tmp_path / "inner").mkdir()
    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    with TestClient(app).websocket_connect("/ws/terminal") as ws:
        ws.receive_json()  # ready
        ws.send_json({"type": "run", "cmd": "cd inner"})
        for _ in range(50):
            msg = ws.receive_json()
            if msg["type"] == "exit":
                assert Path(msg["cwd"]) == (tmp_path / "inner").resolve()
                break
        else:
            raise AssertionError("no exit message for cd")
