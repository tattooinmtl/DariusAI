"""What each chat turn did to the files, and how to take it back.

Before a turn's first write to a file, its original bytes are kept (or the
fact it didn't exist). At the end of the turn the chat shows a card listing
the changed files with their diffs, and an Undo button that restores the
originals. Undo refuses a file that was changed again after the turn — it
would otherwise throw away your later edits.

Only writes made through the agent's file tools (write_file, edit_file) are
tracked; a shell command that edits files is outside this net.
"""

from __future__ import annotations

import difflib
import hashlib
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

MAX_DIFF_CHARS = 20_000
KEEP_TURNS = 20


def _digest(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


class TurnChanges:
    def __init__(self, root: Path | None) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.root = root
        self.before: "OrderedDict[str, bytes | None]" = OrderedDict()
        self.after: dict[str, str | None] = {}

    def record(self, path: Path) -> None:
        """Call before writing `path`. Only the first call per turn keeps
        the original — later writes in the same turn build on it."""
        key = str(path)
        if key not in self.before:
            self.before[key] = _read(path)

    def label(self, key: str) -> str:
        p = Path(key)
        if self.root is not None:
            try:
                return p.relative_to(self.root).as_posix()
            except ValueError:
                pass
        return p.as_posix()

    def summary(self) -> list[dict[str, Any]]:
        """Changed files with stats and a unified diff; also stamps what
        each file looks like now, which undo checks against."""
        files = []
        for key, old in self.before.items():
            new = _read(Path(key))
            self.after[key] = _digest(new)
            if old == new:
                continue
            old_t = (old or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
            new_t = (new or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
            name = self.label(key)
            diff_lines = list(difflib.unified_diff(old_t, new_t, f"a/{name}", f"b/{name}", n=3))
            diff = "".join(diff_lines)
            if len(diff) > MAX_DIFF_CHARS:
                diff = diff[:MAX_DIFF_CHARS] + "\n… diff truncated …\n"
            files.append({
                "path": name,
                "status": "added" if old is None else ("deleted" if new is None else "modified"),
                "added": sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++")),
                "removed": sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---")),
                "diff": diff,
            })
        return files

    def undo(self) -> dict[str, list[str]]:
        restored, skipped = [], []
        for key, old in self.before.items():
            path = Path(key)
            now = _read(path)
            if key in self.after and _digest(now) != self.after[key]:
                skipped.append(self.label(key))      # changed again since: keep the newer edit
                continue
            if old is None:
                if now is not None:
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(old)
            restored.append(self.label(key))
        return {"restored": restored, "skipped": skipped}


class ChangeTracker:
    """The registry's view: the turn being recorded, and the last few
    finished turns that can still be undone."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: TurnChanges | None = None
        self.done: "OrderedDict[str, TurnChanges]" = OrderedDict()

    def begin(self, root: Path | None) -> None:
        with self._lock:
            self.current = TurnChanges(root)

    def record(self, path: Path) -> None:
        with self._lock:
            if self.current is not None:
                self.current.record(path)

    def finish(self) -> dict[str, Any] | None:
        """End the turn; returns the card payload, or None if no file changed."""
        with self._lock:
            turn, self.current = self.current, None
        if turn is None or not turn.before:
            return None
        files = turn.summary()
        if not files:
            return None
        with self._lock:
            self.done[turn.id] = turn
            while len(self.done) > KEEP_TURNS:
                self.done.popitem(last=False)
        return {"type": "turn_changes", "turn": turn.id, "files": files}

    def undo(self, turn_id: str) -> dict[str, Any]:
        with self._lock:
            turn = self.done.pop(turn_id, None)
        if turn is None:
            return {"type": "undo_result", "turn": turn_id, "error": "that turn can no longer be undone"}
        return {"type": "undo_result", "turn": turn_id, **turn.undo()}
