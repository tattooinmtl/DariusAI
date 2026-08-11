"""The sandbox: the boundary the agent's filesystem and shell tools run inside.

Until this existed the agent had no boundary at all. The *editor* was
confined to the project directory (viz/server._safe_path), but the agent —
the part that acts on its own, at speed, from a model's judgement — could
read any path on the machine, including the brain's own database of
DPAPI-encrypted API keys, and could run any command anywhere. The safe
component was the one with a human driving it.

What this actually provides
---------------------------
1. **Confinement.** Every path a tool touches is resolved and checked to be
   inside the sandbox root. Resolution happens before the check, so `..`,
   symlinks and absolute paths can't step outside. Absolute paths that are
   already inside are fine — the agent uses those legitimately.

2. **Process-tree kill.** Commands run inside a Windows job object with
   KILL_ON_JOB_CLOSE. A plain `subprocess` timeout kills only the process it
   launched; a shell that started a dev server leaves that server running
   forever. Killing the job kills the whole tree, which is the difference
   between a timeout and a leak. (This project has already been bitten by
   exactly that failure with its own launcher.)

3. **Credential scrubbing.** The agent's subprocesses don't inherit the
   parent's API keys. A build script, a postinstall hook or a test suite has
   no business seeing them.

What it deliberately does not claim
-----------------------------------
This is containment against a *model's mistakes*, not a security boundary
against an adversary. A command that runs inside the root can still do
anything a user could do inside the root, and `shell=True` means the shell's
own escapes exist. Real isolation needs a VM or container: Windows Sandbox
is Pro/Enterprise only, and Docker isn't a dependency this project has, so
neither is assumed. `docker_available()` is exposed for callers that want to
escalate when it happens to be there — not required, per "if it isn't needed
don't do it".
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 60
MAX_SHELL_OUTPUT = 20_000

# Anything matching these is stripped from a subprocess's environment. Broad
# on purpose: a false positive costs a build script one variable it probably
# shouldn't have had, a false negative leaks a credential.
_SECRET_PATTERN = re.compile(r"(API[_-]?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|_KEY$)", re.IGNORECASE)

CREATE_NEW_PROCESS_GROUP = 0x00000200
# Without this, every console program this app launches pops a console
# window onto the desktop. The app runs under pythonw.exe, which has no
# console of its own, so Windows allocates a fresh one per child and it
# flashes open and shut. Eleven runtime probes at startup meant eleven
# flashes. Redirecting the streams (capture_output) does not prevent it —
# only this flag does.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def quiet_creationflags(extra: int = 0) -> int:
    """Creation flags for any child process this app spawns. Every spawn
    site should use it — a stray subprocess is a visible window."""
    if sys.platform != "win32":
        return 0
    return CREATE_NO_WINDOW | extra


class SandboxViolation(Exception):
    """A tool tried to touch something outside the sandbox root."""


@dataclass
class ShellResult:
    returncode: int
    output: str
    timed_out: bool = False

    def to_text(self, limit: int = MAX_SHELL_OUTPUT) -> str:
        out = self.output
        if len(out) > limit:
            out = out[:limit] + "\n…(truncated)"
        prefix = "TIMED OUT — process tree killed\n" if self.timed_out else ""
        return f"{prefix}exit={self.returncode}\n{out}"


@dataclass
class Sandbox:
    """A rooted execution boundary. `root=None` means no confinement, which
    is spelled `Sandbox.unrestricted()` so it can never be arrived at by
    accident — an unconfined agent should be a decision someone made."""

    root: Path | None
    timeout: int = DEFAULT_TIMEOUT
    memory_mb: int | None = 2048
    max_processes: int | None = 64
    scrub_secrets: bool = True

    def __post_init__(self) -> None:
        if self.root is not None:
            self.root = Path(self.root).resolve()

    @classmethod
    def unrestricted(cls) -> "Sandbox":
        return cls(root=None)

    @classmethod
    def for_workspace(cls, project_dir: Path | str, workbench: Path | str | None = None, **kwargs) -> "Sandbox":
        """The agent's boundary for an all-in-one workbench.

        When the open project sits inside the workbench, the workbench is
        the root — so the agent can list sibling projects, create new ones
        and move work between them, which is the whole point of having one.
        When the user has opened a folder somewhere else entirely, that
        folder is the root and nothing wider: an unrelated directory is not
        an invitation to roam the disk.
        """
        project = Path(project_dir).resolve()
        if workbench:
            bench = Path(workbench).resolve()
            if project == bench or bench in project.parents:
                return cls(root=bench, **kwargs)
        return cls(root=project, **kwargs)

    @property
    def confined(self) -> bool:
        return self.root is not None

    # -- paths --------------------------------------------------------------

    def resolve(self, path: str | Path) -> Path:
        """Resolve a tool-supplied path inside the sandbox, or raise.

        Resolution comes first so that `..` segments and symlinks are already
        collapsed by the time containment is checked — checking the string
        instead of the resolved path is the classic way this gets bypassed.
        """
        if not self.confined:
            return Path(path)

        candidate = Path(path)
        candidate = (self.root / candidate) if not candidate.is_absolute() else candidate
        # A path that doesn't exist yet still has to resolve — strict=False
        # walks as far as it can and normalises the rest.
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise SandboxViolation(
                f"path escapes the sandbox: {path!r} resolves outside {self.root}"
            )
        return resolved

    # -- environment --------------------------------------------------------

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.scrub_secrets:
            for name in [k for k in env if _SECRET_PATTERN.search(k)]:
                env.pop(name, None)
        return env

    # -- commands -----------------------------------------------------------

    def run_streaming(self, command: str, on_line, cwd: str | Path | None = None,
                      timeout: int | None = None) -> ShellResult:
        """Same guarantees as run(), but hands each output line to `on_line`
        as it arrives.

        An installer is the case that needs this: `pip install` or `npm
        install` can take a minute, and a console that prints everything
        only once it's finished is indistinguishable from one that's hung.
        """
        workdir = self.resolve(cwd) if cwd else (self.root or Path.cwd())
        limit = timeout or self.timeout
        job = _create_job(self.memory_mb, self.max_processes) if sys.platform == "win32" else None

        proc = subprocess.Popen(
            command, shell=True, cwd=str(workdir), env=self.environment(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            errors="replace", bufsize=1,
            creationflags=quiet_creationflags(CREATE_NEW_PROCESS_GROUP),
        )
        if job:
            _assign_to_job(job, proc)

        collected: list[str] = []
        deadline = time.monotonic() + limit
        timed_out = False
        try:
            for line in proc.stdout or []:
                line = line.rstrip("\n")
                collected.append(line)
                on_line(line)
                if time.monotonic() > deadline:
                    timed_out = True
                    _kill_tree(job, proc)
                    on_line(f"[timed out after {limit}s — process tree killed]")
                    break
            proc.wait(timeout=5)
        except Exception as exc:
            on_line(f"[error: {exc}]")
        finally:
            if job:
                _close_job(job)

        return ShellResult(returncode=proc.returncode if proc.returncode is not None else -1,
                           output="\n".join(collected), timed_out=timed_out)

    def run(self, command: str, cwd: str | Path | None = None, timeout: int | None = None) -> ShellResult:
        workdir = self.resolve(cwd) if cwd else (self.root or Path.cwd())
        if self.confined and not Path(workdir).is_dir():
            raise SandboxViolation(f"working directory does not exist: {workdir}")

        limit = timeout or self.timeout
        job = _create_job(self.memory_mb, self.max_processes) if sys.platform == "win32" else None

        proc = subprocess.Popen(
            command, shell=True, cwd=str(workdir), env=self.environment(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            errors="replace",
            creationflags=quiet_creationflags(CREATE_NEW_PROCESS_GROUP),
        )
        if job:
            # Assigned immediately after creation rather than at creation
            # time: subprocess closes the thread handle, so the usual
            # CREATE_SUSPENDED-then-assign-then-resume dance isn't available.
            # A process that forks within microseconds could escape the job;
            # for build and test commands that's a theoretical gap, and the
            # alternative is reimplementing CreateProcess.
            _assign_to_job(job, proc)

        timed_out = False
        try:
            output = proc.communicate(timeout=limit)[0] or ""
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(job, proc)
            output = (proc.communicate()[0] or "") if proc.stdout else ""
        finally:
            if job:
                _close_job(job)  # KILL_ON_JOB_CLOSE reaps anything still alive

        return ShellResult(returncode=proc.returncode if proc.returncode is not None else -1,
                           output=output, timed_out=timed_out)


# ---- Windows job objects ---------------------------------------------------
# ctypes rather than pywin32: this is four calls, and the project has no COM
# dependency to justify adding one.

def _create_job(memory_mb: int | None, max_processes: int | None):
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                        ("WriteOperationCount", ctypes.c_ulonglong),
                        ("OtherOperationCount", ctypes.c_ulonglong),
                        ("ReadTransferCount", ctypes.c_ulonglong),
                        ("WriteTransferCount", ctypes.c_ulonglong),
                        ("OtherTransferCount", ctypes.c_ulonglong)]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x0008
        JOB_OBJECT_LIMIT_JOB_MEMORY = 0x0200
        JobObjectExtendedLimitInformation = 9

        job = ctypes.windll.kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if max_processes:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = max_processes
        if memory_mb:
            flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory_mb * 1024 * 1024
        info.BasicLimitInformation.LimitFlags = flags

        ctypes.windll.kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        )
        return job
    except Exception:
        return None  # no job object is a weaker sandbox, not a broken one


def _assign_to_job(job, proc) -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.AssignProcessToJobObject(job, int(proc._handle))
    except Exception:
        pass


def _kill_tree(job, proc) -> None:
    if job:
        try:
            import ctypes
            ctypes.windll.kernel32.TerminateJobObject(job, 1)
            return
        except Exception:
            pass
    try:
        proc.kill()  # fallback: parent only, which is why the job exists
    except Exception:
        pass


def _close_job(job) -> None:
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(job)
    except Exception:
        pass


def docker_available() -> bool:
    """Whether a container runtime happens to be present. Nothing requires
    it; it's here so a caller can escalate isolation when it's free to."""
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, timeout=8,
                              creationflags=quiet_creationflags())
        return proc.returncode == 0
    except Exception:
        return False
