"""Running a code block the assistant wrote.

Every language here runs a *single file* with one command — no project
scaffolding, no build config. That's the constraint that decides the list:
`go run x.go`, `java X.java` (single-file source since Java 11) and
`dotnet run x.cs` (single-file C# since .NET 10) qualify; Rust doesn't,
because cargo wants a project around it.

Detection is real, not assumed. Each runtime is probed once and the result
cached, so the UI can offer Run only for languages this machine can
actually run. A button that appears and then fails is worse than no button.

SQL is the interesting one: the `sqlite3` CLI is frequently absent
(including on this machine), but Python ships the `sqlite3` module in its
standard library. So a SQL block runs through a small generated Python
script against a scratch database file in the project — no install, and the
database persists as a real artifact you can keep querying.

Execution always goes through the Sandbox: confined to the project
directory, credentials stripped from the environment, and a timeout that
kills the whole process tree. Running model-written code is precisely what
that was built for.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .sandbox import Sandbox, ShellResult, quiet_creationflags

SCRATCH_DIRNAME = ".dariusai-scratch"
SNIPPET_TIMEOUT = 30


@dataclass(frozen=True)
class Runtime:
    language: str
    extension: str
    probe: list[str]          # command that proves the toolchain exists
    template: str             # how to run one file; {file} is substituted
    label: str
    aliases: tuple[str, ...] = ()
    note: str = ""
    # Extra places to look when the name on PATH isn't the real thing. On
    # Windows `bash` resolves to the WSL stub, which reports "no installed
    # distributions" and exits non-zero even though git-bash is right there.
    fallback_binaries: tuple[str, ...] = ()


RUNTIMES: tuple[Runtime, ...] = (
    Runtime("python", ".py", ["python", "--version"], 'python "{file}"', "Python",
            aliases=("py", "python3")),
    Runtime("javascript", ".js", ["node", "--version"], 'node "{file}"', "Node.js",
            aliases=("js", "node")),
    Runtime("typescript", ".ts", ["node", "--version"], 'node "{file}"', "TypeScript",
            aliases=("ts",), note="Node 24+ strips types natively — no ts-node needed"),
    Runtime("php", ".php", ["php", "--version"], 'php "{file}"', "PHP"),
    Runtime("ruby", ".rb", ["ruby", "--version"], 'ruby "{file}"', "Ruby", aliases=("rb",)),
    Runtime("powershell", ".ps1", ["pwsh", "--version"], 'pwsh -NoProfile -File "{file}"',
            "PowerShell", aliases=("ps1", "pwsh")),
    Runtime("bash", ".sh", ["bash", "--version"], '"{bin}" "{file}"', "Bash",
            aliases=("sh", "shell", "zsh"),
            fallback_binaries=("C:/Program Files/Git/bin/bash.exe",
                               "C:/Program Files/Git/usr/bin/bash.exe",
                               "C:/Program Files (x86)/Git/bin/bash.exe")),
    Runtime("go", ".go", ["go", "version"], 'go run "{file}"', "Go", aliases=("golang",)),
    Runtime("java", ".java", ["java", "-version"], 'java "{file}"', "Java",
            note="single-file source execution"),
    Runtime("csharp", ".cs", ["dotnet", "--version"], 'dotnet run "{file}"', "C#",
            aliases=("cs", "c#"), note="single-file C#, .NET 10+"),
    # Handled specially below — the runner is generated, not a bare command.
    Runtime("sql", ".sql", ["python", "--version"], "", "SQLite (via Python)",
            aliases=("sqlite", "sqlite3"),
            note="uses Python's built-in sqlite3 module; no sqlite3 CLI required"),
)

_BY_NAME: dict[str, Runtime] = {}
for _rt in RUNTIMES:
    _BY_NAME[_rt.language] = _rt
    for _alias in _rt.aliases:
        _BY_NAME[_alias] = _rt

# Languages that have no runtime because they don't need one — offering
# "Run" for them would be a lie; they want a preview, which is a different
# feature.
NOT_EXECUTABLE = {"html", "css", "json", "yaml", "yml", "markdown", "md", "text", "xml", "toml", "ini", "diff"}

_detection_cache: dict[str, dict[str, Any]] | None = None


def runtime_for(language: str) -> Runtime | None:
    return _BY_NAME.get((language or "").strip().lower())


def _try_binary(binary: str, probe: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run([binary] + probe[1:], capture_output=True, text=True, timeout=12,
                              creationflags=quiet_creationflags())
    except Exception:
        return False, ""
    # `java -version` writes to stderr; take whichever stream spoke.
    text = (proc.stdout or "") + (proc.stderr or "")
    first = text.strip().splitlines()[0] if text.strip() else ""
    # The WSL stub answers a `bash --version` probe successfully-ish while
    # being unable to run anything. Treat it as absent.
    if "windows subsystem for linux" in text.lower():
        return False, ""
    return proc.returncode == 0, first[:80]


def _probe(runtime: Runtime) -> tuple[bool, str, str]:
    """Returns (available, version, binary). The binary matters because the
    name on PATH is not always the thing that works."""
    candidates = []
    on_path = shutil.which(runtime.probe[0])
    if on_path:
        candidates.append(on_path)
    candidates.extend(b for b in runtime.fallback_binaries if Path(b).exists())

    for binary in candidates:
        ok, version = _try_binary(binary, runtime.probe)
        if ok:
            return True, version, binary
    return False, "", ""


def detect(refresh: bool = False) -> dict[str, dict[str, Any]]:
    """Probe every runtime once. Cached, because shelling out eleven times
    on each render of a chat message would be absurd."""
    global _detection_cache
    if _detection_cache is not None and not refresh:
        return _detection_cache

    found: dict[str, dict[str, Any]] = {}
    for runtime in RUNTIMES:
        available, version, binary = _probe(runtime)
        found[runtime.language] = {
            "binary": binary,
            "language": runtime.language,
            "label": runtime.label,
            "available": available,
            "version": version,
            "extension": runtime.extension,
            "aliases": list(runtime.aliases),
            "note": runtime.note,
        }
    _detection_cache = found
    return found


def is_runnable(language: str) -> bool:
    runtime = runtime_for(language)
    if runtime is None:
        return False
    return detect()[runtime.language]["available"]


def scratch_dir(root: Path) -> Path:
    path = Path(root) / SCRATCH_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sql_runner(sql_path: Path, db_path: Path) -> str:
    """A tiny Python program that runs a .sql script and prints any rows.

    Written to disk rather than passed with `python -c` so the command shown
    to the user stays readable, and so a syntax error points at a real file.
    """
    return (
        "import sqlite3, sys\n"
        f"con = sqlite3.connect(r'''{db_path}''')\n"
        f"script = open(r'''{sql_path}''', encoding='utf-8').read()\n"
        "cur = con.cursor()\n"
        "try:\n"
        "    cur.executescript(script)\n"
        "except sqlite3.Error as exc:\n"
        "    print('SQL error:', exc); sys.exit(1)\n"
        "for statement in [s.strip() for s in script.split(';') if s.strip()]:\n"
        "    if statement.lower().startswith('select'):\n"
        "        cur.execute(statement)\n"
        "        cols = [d[0] for d in cur.description or []]\n"
        "        if cols: print(' | '.join(cols))\n"
        "        for row in cur.fetchall():\n"
        "            print(' | '.join('' if v is None else str(v) for v in row))\n"
        "con.commit()\n"
        f"print('\\n[database: {db_path.name}]')\n"
    )


def write_snippet(root: Path, language: str, code: str, name: str | None = None) -> Path:
    """Put a snippet on disk inside the project's scratch directory, which is
    where it has to be anyway to be run or opened in the editor."""
    runtime = runtime_for(language)
    extension = runtime.extension if runtime else ".txt"
    folder = scratch_dir(root)
    if name:
        target = folder / name
    else:
        existing = len(list(folder.glob(f"snippet-*{extension}"))) + 1
        target = folder / f"snippet-{existing}{extension}"
    target.write_text(code, encoding="utf-8")
    return target


def run_snippet(sandbox: Sandbox, language: str, code: str, timeout: int = SNIPPET_TIMEOUT) -> dict[str, Any]:
    """Write the snippet, run it in the sandbox, and report what was run.

    The command is returned alongside the output because the user is about
    to be shown the result of executing model-generated code — being able
    to see exactly what ran is not optional.
    """
    runtime = runtime_for(language)
    if runtime is None:
        return {"ok": False, "error": f"no runtime for {language!r}", "command": "", "output": "", "exit": -1}
    if not detect()[runtime.language]["available"]:
        return {
            "ok": False,
            "error": f"{runtime.label} isn't installed on this machine",
            "command": "", "output": "", "exit": -1,
        }

    root = sandbox.root or Path.cwd()
    path = write_snippet(root, language, code)

    if runtime.language == "sql":
        db = scratch_dir(root) / "scratch.db"
        runner = scratch_dir(root) / "_run_sql.py"
        runner.write_text(_sql_runner(path, db), encoding="utf-8")
        command = f'python "{runner}"'
    else:
        command = runtime.template.format(file=path, bin=detect()[runtime.language]["binary"])

    result: ShellResult = sandbox.run(command, timeout=timeout)
    return {
        "ok": result.returncode == 0 and not result.timed_out,
        "command": command,
        "output": result.output,
        "exit": result.returncode,
        "timed_out": result.timed_out,
        "file": str(path),
        "error": "",
    }
