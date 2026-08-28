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
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT = 60
MAX_SHELL_OUTPUT = 20_000

# Anything matching these is stripped from a subprocess's environment. Broad
# on purpose: a false positive costs a build script one variable it probably
# shouldn't have had, a false negative leaks a credential.
_SECRET_PATTERN = re.compile(r"(API[_-]?KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|_KEY$)", re.IGNORECASE)

# Commands that mutate or delete on the shells this app talks to. Applied
# only inside externally granted trees — the primary root has always allowed
# them because it's the agent's own workspace. First-token match on each
# subcommand (split on ; && || | \n), so `git clean` is caught by matching
# both tokens; `rm -rf …` is caught by the first. Not adversarial-strength —
# `sh -c "rm x"` slips past — matching the module's stated stance.
_DESTRUCTIVE_FIRST_TOKENS = frozenset({
    "rm", "del", "erase", "rmdir", "rd", "unlink",
    "mv", "move", "ren", "rename",
    "truncate", "shred", "dd",
    "format", "diskpart",
    "remove-item", "ri", "clear-content", "clc",
})
_DESTRUCTIVE_PAIRS = frozenset({
    ("git", "clean"),
    ("git", "reset"),   # `git reset --hard` is the case; a plain `git reset` is a soft reset but this project stays strict inside grants
    ("git", "rm"),
    ("git", "restore"),
    ("git", "checkout"),
    ("npm", "uninstall"),
    ("pip", "uninstall"),
    ("cargo", "clean"),
})
_SUBCOMMAND_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")

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


class PermissionBroker:
    """The mechanism that asks the human whether to allow one external read
    grant. Pluggable so the viz surfaces a modal and the terminal REPL
    surfaces a prompt — the sandbox never blocks on I/O itself.

    `request` must return True to allow, False to deny. Implementations may
    block; the tool call is already on a background thread.
    """

    def request(self, path: str, reason: str) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError


class DenyAllBroker(PermissionBroker):
    """The safe default: no UI wired in, no grants possible."""

    def request(self, path: str, reason: str) -> bool:
        return False


class TerminalBroker(PermissionBroker):
    """Blocking prompt on stdin/stdout — for the CLI REPL."""

    def request(self, path: str, reason: str) -> bool:
        print("\n[permission] the agent wants to read outside its sandbox:")
        print(f"  path:   {path}")
        print(f"  reason: {reason}")
        try:
            ans = input("  allow for this turn only? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("  denied (no answer)")
            return False
        return ans in ("y", "yes")


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
    # External read-only grants: extra directory trees the agent can read
    # for the current user turn, after the human approves via `broker`.
    # Cleared by `clear_external_grants()` at the top of each new turn so
    # a grant never leaks across prompts (that's the "one time" contract).
    # Writes into a grant are refused; only reads and non-destructive
    # shell commands are allowed.
    external_grants: list[Path] = field(default_factory=list)
    broker: PermissionBroker | None = None

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

    def resolve(self, path: str | Path, for_write: bool = False) -> Path:
        """Resolve a tool-supplied path inside the sandbox, or raise.

        Resolution comes first so that `..` segments and symlinks are already
        collapsed by the time containment is checked — checking the string
        instead of the resolved path is the classic way this gets bypassed.

        `for_write=True` refuses external grants: grants are read-only
        windows into other projects, never write surfaces.
        """
        if not self.confined:
            return Path(path)

        candidate = Path(path)
        candidate = (self.root / candidate) if not candidate.is_absolute() else candidate
        # A path that doesn't exist yet still has to resolve — strict=False
        # walks as far as it can and normalises the rest.
        resolved = candidate.resolve()
        if resolved == self.root or self.root in resolved.parents:
            return resolved
        if not for_write:
            for grant in self.external_grants:
                if resolved == grant or grant in resolved.parents:
                    return resolved
        raise SandboxViolation(
            f"path escapes the sandbox: {path!r} resolves outside {self.root}"
            + (" (writes into external grants are refused)" if for_write and self._inside_any_grant(resolved) else "")
        )

    def _inside_any_grant(self, resolved: Path) -> bool:
        return any(resolved == g or g in resolved.parents for g in self.external_grants)

    # -- external grants ----------------------------------------------------

    def grant_external(self, path: str | Path) -> Path:
        """Add a directory tree the agent may read from this turn onwards.

        Validated to keep grants narrow: the path must exist and be a
        directory; it may not be an ancestor of the sandbox root (that
        would let the agent read the workspace's parents through a
        side door); it may not be a parent of an existing grant (no
        widening a session by re-granting a bigger folder above it).
        Duplicate grants are silently ignored.

        Returns the resolved path so callers can log or display it.
        """
        target = Path(path).resolve()
        if not target.exists() or not target.is_dir():
            raise SandboxViolation(f"grant target is not an existing directory: {target}")
        if self.root is not None and (target == self.root or target in self.root.parents):
            raise SandboxViolation(
                f"grant would cover the sandbox root or its parents: {target}"
            )
        for existing in self.external_grants:
            if existing == target:
                return existing
            if target in existing.parents:
                # New grant is a parent of an existing one → widening.
                raise SandboxViolation(
                    f"grant {target} would widen the existing grant {existing}"
                )
        self.external_grants.append(target)
        return target

    def clear_external_grants(self) -> None:
        """Drop every grant. Called at the top of each user turn so
        approval never carries into the next prompt."""
        self.external_grants.clear()

    def request_and_grant(self, path: str | Path, reason: str) -> tuple[bool, str]:
        """Ask the broker; on approval, add the grant. Returns a
        (granted, message) tuple the caller can hand straight back to the
        agent as a tool result.

        The path is validated *before* prompting so the human is not asked
        to approve something the sandbox would then reject.
        """
        try:
            target = Path(path).resolve()
        except (OSError, ValueError) as exc:
            return False, f"ERROR: cannot resolve path: {exc}"
        if not target.exists() or not target.is_dir():
            return False, f"ERROR: not a directory: {target}"
        if self.root is not None and (target == self.root or target in self.root.parents):
            return False, f"ERROR: refuses to grant an ancestor of the sandbox root ({target})"
        for existing in self.external_grants:
            if existing == target or target in existing.parents:
                return False, f"ERROR: would widen an existing grant ({existing})"
            if existing in target.parents:
                # Already covered — no need to re-ask.
                return True, f"already granted (covered by {existing})"

        broker = self.broker or DenyAllBroker()
        approved = False
        try:
            approved = broker.request(str(target), reason or "(no reason given)")
        except Exception as exc:
            return False, f"ERROR: permission broker failed: {exc}"
        if not approved:
            return False, f"denied by user: {target}"
        try:
            self.grant_external(target)
        except SandboxViolation as exc:
            return False, f"ERROR: {exc}"
        return True, (
            f"granted read-only access to {target} for this turn only "
            f"(reads and non-destructive commands allowed; writes and destructive "
            f"commands like rm/del/mv/git-reset are still refused)"
        )

    # -- destructive-command scanner ---------------------------------------

    def _touches_grant(self, cwd: Path, command: str) -> Path | None:
        """Whether a shell run should be scanned for destructive tokens.

        A run counts as touching a grant if its cwd sits inside one, or the
        command string mentions the grant path literally. Returns the
        matched grant (for the error message) or None.
        """
        for grant in self.external_grants:
            if cwd == grant or grant in cwd.parents:
                return grant
            if str(grant) in command:
                return grant
        return None

    def _check_destructive(self, command: str, grant: Path) -> None:
        """Refuse commands that would mutate anything inside a grant.

        Splits the command on `;`, `&&`, `||`, `|` and newlines, then checks
        each subcommand's first token against a blocklist. Also refuses `>`
        and `>>` redirections whose target resolves into the grant.
        """
        for sub in _SUBCOMMAND_SPLIT.split(command):
            sub = sub.strip()
            if not sub:
                continue
            tokens = sub.split()
            if not tokens:
                continue
            first = tokens[0].lower().lstrip("./\\")
            first = first.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if first in _DESTRUCTIVE_FIRST_TOKENS:
                raise SandboxViolation(
                    f"refused: {first!r} is destructive and the cwd/command touches the "
                    f"external grant {grant} (which is read-only)"
                )
            if len(tokens) >= 2:
                pair = (first, tokens[1].lower())
                if pair in _DESTRUCTIVE_PAIRS:
                    raise SandboxViolation(
                        f"refused: {' '.join(pair)!r} is destructive and the cwd/command "
                        f"touches the external grant {grant} (which is read-only)"
                    )
            # Output redirection into a grant path — `something > /grant/x`.
            for i, tok in enumerate(tokens):
                if tok in (">", ">>") and i + 1 < len(tokens):
                    target = tokens[i + 1].strip('"').strip("'")
                    try:
                        resolved = Path(target)
                        if not resolved.is_absolute():
                            continue  # relative → resolved against cwd already checked
                        resolved = resolved.resolve()
                    except (OSError, ValueError):
                        continue
                    if resolved == grant or grant in resolved.parents:
                        raise SandboxViolation(
                            f"refused: output redirection into external grant {grant} "
                            f"(which is read-only)"
                        )

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
        grant = self._touches_grant(Path(workdir), command) if self.external_grants else None
        if grant is not None:
            self._check_destructive(command, grant)
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

        grant = self._touches_grant(Path(workdir), command) if self.external_grants else None
        if grant is not None:
            self._check_destructive(command, grant)

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
