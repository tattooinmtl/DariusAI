"""The project creation engine.

Creating a project is: pick a folder under the workbench root, write the
template's files, initialise a database if the type wants one, then run the
setup steps — version checks, `python -m venv`, `npm install` — streaming
every line to the caller so the form's embedded console shows the
environment being built as it happens.

Every step runs through the Sandbox, confined to the new project folder.
That means a template can't write outside the project it's creating, its
setup commands inherit no API keys, and a hung installer gets its whole
process tree killed rather than leaking (see agent/sandbox.py).

Emitted events are plain dicts so they can go straight down a websocket:

    {"type": "log",  "line": "...", "stream": "out"|"cmd"|"err"|"ok"}
    {"type": "step", "label": "...", "index": 1, "total": 4}
    {"type": "done", "ok": True, "path": "...", "project": "..."}
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from ..agent.runtimes import detect
from ..agent.sandbox import Sandbox
from .templates import BY_ID, Template, common_files

# The user asked for the workbench to live at the drive root rather than
# inside the app, so projects aren't tangled up with DariusAI's own source.
DEFAULT_WORKBENCH_ROOT = Path("C:/DariusAIWorkbench")
SETUP_TIMEOUT = 600  # a cold `pip install` or `npm install` is genuinely slow

Emit = Callable[[dict[str, Any]], None]

_SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._-]")


class ProjectExists(Exception):
    pass


class InvalidProjectName(Exception):
    pass


def workbench_root(store=None) -> Path:
    """Where projects live. Overridable through the `workbench_root` setting
    so it isn't hardcoded to one machine's layout."""
    if store is not None:
        saved = store.get_setting("workbench_root", "") or ""
        if saved:
            return Path(saved).expanduser()
    return DEFAULT_WORKBENCH_ROOT


def sanitize_name(name: str) -> str:
    """A project name becomes a folder name, so it has to survive being one.
    Rejecting outright beats silently creating a folder the user didn't ask
    for — but stripping stray punctuation is not worth an error."""
    cleaned = _SAFE_NAME.sub("", (name or "").strip()).strip(" .")
    cleaned = re.sub(r"\s+", "-", cleaned)
    if not cleaned:
        raise InvalidProjectName("project name must contain a letter or number")
    if len(cleaned) > 64:
        cleaned = cleaned[:64].rstrip("-")
    return cleaned


def project_path(name: str, store=None, root: Path | None = None) -> Path:
    return (Path(root) if root else workbench_root(store)) / sanitize_name(name)


def _init_sqlite(project: Path, emit: Emit) -> None:
    """Create the database from schema.sql using Python's built-in sqlite3 —
    no sqlite3 CLI needed, which is frequently absent on Windows."""
    data = project / "data"
    data.mkdir(parents=True, exist_ok=True)
    db = data / "app.db"
    schema = project / "schema.sql"
    emit({"type": "log", "line": f"$ sqlite3 {db.name} < schema.sql", "stream": "cmd"})
    con = sqlite3.connect(db)
    try:
        if schema.exists():
            con.executescript(schema.read_text(encoding="utf-8"))
        con.commit()
    finally:
        con.close()
    emit({"type": "log", "line": f"created {db.relative_to(project)} ({db.stat().st_size} bytes)", "stream": "ok"})


def create_project(
    name: str,
    template_id: str,
    emit: Emit,
    store=None,
    root: Path | None = None,
    timeout: int = SETUP_TIMEOUT,
) -> dict[str, Any]:
    """Scaffold and set up a project, streaming progress through `emit`."""
    template: Template | None = BY_ID.get(template_id)
    if template is None:
        raise ValueError(f"unknown project type {template_id!r}")

    folder = sanitize_name(name)
    target = (Path(root) if root else workbench_root(store)) / folder
    if target.exists() and any(target.iterdir()):
        raise ProjectExists(f"{target} already exists and isn't empty")

    target.mkdir(parents=True, exist_ok=True)
    emit({"type": "log", "line": f"workbench: {target.parent}", "stream": "out"})
    emit({"type": "log", "line": f"creating {folder} ({template.label})", "stream": "ok"})

    # --- files ------------------------------------------------------------
    files = {**common_files(folder, template), **template.files}
    for rel, content in sorted(files.items()):
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        emit({"type": "log", "line": f"  + {rel}", "stream": "out"})

    if template.sqlite:
        _init_sqlite(target, emit)

    # --- runtime availability --------------------------------------------
    if template.runtime:
        info = detect().get(template.runtime, {})
        if not info.get("available"):
            emit({"type": "log",
                  "line": f"{info.get('label', template.runtime)} is not installed — "
                          f"files were created, but setup steps were skipped.",
                  "stream": "err"})
            return {"ok": False, "path": str(target), "project": folder,
                    "reason": f"{template.runtime} not installed"}
        emit({"type": "log", "line": f"{info.get('label')} detected: {info.get('version')}", "stream": "ok"})

    # --- setup steps ------------------------------------------------------
    sandbox = Sandbox(root=target, timeout=timeout)
    total = len(template.steps)
    failed = False
    for index, (label, command) in enumerate(template.steps, start=1):
        emit({"type": "step", "label": label, "index": index, "total": total})
        emit({"type": "log", "line": f"$ {command}", "stream": "cmd"})
        result = sandbox.run_streaming(
            command,
            lambda line: emit({"type": "log", "line": line, "stream": "out"}),
            timeout=timeout,
        )
        if result.returncode != 0:
            failed = True
            emit({"type": "log", "line": f"[exit {result.returncode}] {label} failed", "stream": "err"})
            break
        emit({"type": "log", "line": f"✓ {label}", "stream": "ok"})

    if not failed:
        emit({"type": "log", "line": "project ready.", "stream": "ok"})
    return {"ok": not failed, "path": str(target), "project": folder}
