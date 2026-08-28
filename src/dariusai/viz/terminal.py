"""A real shell session behind the app's Terminal panel.

Line-oriented rather than a PTY: each command is spawned through the
platform shell, its merged stdout/stderr streams back as it is produced,
and the session carries a working directory across commands so ``cd``
behaves the way it does in any terminal. A PTY would additionally buy
full-screen programs (vim, top) at the cost of a compiled dependency on
Windows (pywinpty); everything the panel actually exists for — builds,
tests, git, the CLI — is line-oriented and works without it.

This runs whatever the user types, unsandboxed, in the project directory.
That is what a terminal is, and it is reachable only from the app's own
window on 127.0.0.1 — the same trust boundary as the desktop shell that
launched the app.
"""

from __future__ import annotations

import codecs
import os
import platform
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .. import VERSION_DISPLAY, __version__

# The job-object dance (create, assign, terminate the whole tree) is already
# solved in the sandbox module; a second copy here would be one more place
# for a process leak to hide.
from ..agent.sandbox import (
    CREATE_NEW_PROCESS_GROUP,
    _assign_to_job,
    _close_job,
    _create_job,
    _kill_tree,
    quiet_creationflags,
)

# Colour/cursor escapes: the panel renders plain text, so strip them rather
# than printing raw control sequences. NO_COLOR is set for the child too,
# but plenty of tools ignore it.
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

BUILTIN_HELP = """Built-ins — everything else is handed to the shell.

  --info, -i      DariusAI specs: version, runtime, brain, project, provider
  --version, -v   version only
  --help, -h      this list
  cd <dir>        change directory; it sticks for the next command
  clear, cls      clear the pane

Ctrl-C (or Stop) kills a running command; while one is running, what you
type is sent to its stdin. Up/Down walks your command history.
"""


def shell_name() -> str:
    """The shell commands are handed to, for display in the panel."""
    if os.name == "nt":
        return Path(os.environ.get("COMSPEC", "cmd.exe")).name
    return os.environ.get("SHELL", "/bin/sh")


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    # Unbuffered + utf-8 so Python children stream as they go and decode
    # cleanly, instead of arriving in one lump when the process exits.
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    env["FORCE_COLOR"] = "0"
    env["TERM"] = "dumb"
    return env


def _process_group_kwargs() -> dict:
    """POSIX wants its own session so a signal reaches the whole pipeline;
    Windows gets the same effect from the process group + job object, and
    rejects start_new_session outright."""
    return {} if sys.platform == "win32" else {"start_new_session": True}


class TerminalSession:
    """One shell session: a working directory plus at most one live process.

    `info` supplies the extra rows `--info` prints (brain, project, provider,
    uptime) — the session itself knows nothing about the running app, and
    without one it still reports what it can see from here.
    """

    def __init__(self, cwd: Path | str, info: Callable[[], dict[str, str]] | None = None) -> None:
        self.cwd = Path(cwd).resolve()
        self._info = info
        self._proc: subprocess.Popen | None = None
        self._job = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    # ---- builtins -------------------------------------------------------
    def _try_builtin(self, cmd: str, on_output: Callable[[str], None]) -> int | None:
        """Commands the session answers itself. Returns None to pass the
        line through to the shell."""
        # `darius --info` is the instinct for anyone who has used a CLI, and
        # `darius` isn't a real program, so accept it as a prefix. `dariusai`
        # deliberately isn't stripped — that IS a real console script, and
        # shadowing it would hide the actual CLI.
        stripped = cmd
        if stripped.split(None, 1)[0].lower() == "darius":
            parts = stripped.split(None, 1)
            stripped = parts[1].strip() if len(parts) > 1 else "--info"

        head = stripped.split(None, 1)[0].lower()
        if head in ("--version", "-v"):
            on_output(f"DariusAI {__version__} ({VERSION_DISPLAY})\n")
            return 0
        if head in ("--info", "-i"):
            on_output(self.render_info())
            return 0
        if head in ("--help", "-h"):
            on_output(BUILTIN_HELP)
            return 0
        return self._try_cd(cmd, on_output)

    def base_info(self) -> dict[str, str]:
        """What the session can report without help from the app."""
        return {
            "Version": f"{VERSION_DISPLAY} ({__version__})",
            "Python": f"{platform.python_version()} ({platform.python_implementation()})",
            "Platform": f"{platform.system()} {platform.release()} · {platform.machine()}",
            "Shell": shell_name(),
            "Directory": str(self.cwd),
        }

    def render_info(self) -> str:
        rows = self.base_info()
        if self._info is not None:
            try:
                rows.update(self._info())
            except Exception as exc:  # never let a broken row kill the command
                rows["Warning"] = f"could not read app state: {exc}"
        width = max(len(k) for k in rows)
        lines = [f"  {key.ljust(width)}   {value}" for key, value in rows.items()]
        return "DariusAI\n" + "\n".join(lines) + "\n"

    def _try_cd(self, cmd: str, on_output: Callable[[str], None]) -> int | None:
        """Handle ``cd`` in-process — a child shell's chdir would die with
        the child. Returns None when the command isn't a cd."""
        parts = cmd.split(None, 1)
        if parts[0].lower() != "cd":
            return None
        arg = parts[1].strip() if len(parts) > 1 else ""
        if len(arg) > 1 and arg[0] in "\"'" and arg[-1] == arg[0]:
            arg = arg[1:-1]
        if not arg:
            on_output(str(self.cwd) + "\n")
            return 0
        target = Path(os.path.expandvars(os.path.expanduser(arg)))
        if not target.is_absolute():
            target = self.cwd / target
        try:
            resolved = target.resolve(strict=True)
        except OSError:
            on_output(f"cd: no such directory: {arg}\n")
            return 1
        if not resolved.is_dir():
            on_output(f"cd: not a directory: {arg}\n")
            return 1
        self.cwd = resolved
        return 0

    # ---- running --------------------------------------------------------
    def run(self, cmd: str, on_output: Callable[[str], None]) -> int:
        """Run one command to completion, streaming its output. Blocking —
        call it off the event loop."""
        cmd = cmd.strip()
        if not cmd:
            return 0

        builtin = self._try_builtin(cmd, on_output)
        if builtin is not None:
            return builtin

        # No memory or process-count limit: this is the user's own shell,
        # not agent-authored code. The job exists purely so Ctrl-C kills the
        # whole tree instead of just the shell that spawned it.
        job = _create_job(None, None) if sys.platform == "win32" else None
        try:
            proc = subprocess.Popen(  # noqa: S602 - a terminal runs what it is told
                cmd,
                shell=True,
                cwd=str(self.cwd),
                env=_child_env(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=quiet_creationflags(CREATE_NEW_PROCESS_GROUP),
                **_process_group_kwargs(),
            )
        except OSError as exc:
            if job:
                _close_job(job)
            on_output(f"{exc}\n")
            return 1

        if job:
            _assign_to_job(job, proc)
        with self._lock:
            self._proc, self._job = proc, job

        # Incremental decoding: a 4 KiB read can land mid-character, and a
        # per-chunk decode would turn every split multi-byte glyph into
        # mojibake.
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        stream = proc.stdout
        try:
            while True:
                chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(1)
                if not chunk:
                    break
                text = _ANSI_RE.sub("", decoder.decode(chunk))
                if text:
                    on_output(text)
            tail = _ANSI_RE.sub("", decoder.decode(b"", True))
            if tail:
                on_output(tail)
            code = proc.wait()
        except OSError as exc:
            on_output(f"[error: {exc}]\n")
            code = proc.poll() if proc.poll() is not None else -1
        finally:
            with self._lock:
                self._proc, self._job = None, None
            for pipe in (proc.stdout, proc.stdin):
                try:
                    if pipe:
                        pipe.close()
                except OSError:
                    pass
            if job:
                _close_job(job)
        return code

    def send_input(self, data: str) -> bool:
        """Feed a line to the running process's stdin."""
        with self._lock:
            proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return False
        try:
            proc.stdin.write(data.encode("utf-8"))
            proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def interrupt(self) -> bool:
        """Ctrl-C: kill the running process and everything it spawned."""
        with self._lock:
            proc, job = self._proc, self._job
        if proc is None or proc.poll() is not None:
            return False
        if sys.platform == "win32":
            _kill_tree(job, proc)
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                proc.terminate()
        return True

    def close(self) -> None:
        """The panel went away or the socket dropped — don't leave the
        command running with nothing reading its output."""
        self.interrupt()
