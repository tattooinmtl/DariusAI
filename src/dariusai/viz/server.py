"""FastAPI backend for the neural-network viz: the graph API, node
read/edit/delete endpoints (per the spec's locked visual spec — click a
node, read or edit its content, save updates the index), and a websocket
that streams live activity so the page can pulse in real time.

Self-contained: the whole page is one static HTML file with inline CSS/JS,
served straight off disk. The editor's CodeMirror dependency is vendored
under static/vendor/ (downloaded once, committed as plain files) rather than
loaded from a CDN — no network access needed at runtime.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import VERSION_DISPLAY, __version__
from ..agent.chat import ChatSession
from ..agent.sandbox import Sandbox
from ..agent.tools import build_tool_registry
from ..brain.skill import Skill, Source
from ..brain.store import CONVERSATIONS_ROOT, COORDINATOR_ID, BrainStore
from ..events.bus import bus

STATIC_DIR = Path(__file__).parent / "static"
MAX_FILE_BYTES = 2_000_000
_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.I)
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")
_STOP_WORDS = {
    "about", "after", "again", "also", "and", "any", "are", "ask", "asked", "but", "can", "calls",
    "could", "did", "does", "for", "from", "have", "here", "how", "into", "just", "links", "live",
    "map", "markers", "more", "need", "only", "please", "remember", "show", "that", "the", "their",
    "them", "then", "there", "these", "they", "this", "topic", "turn", "used", "using", "want", "was",
    "what", "when", "where", "which", "with", "would", "your",
}


def _normalize_subject(text: str) -> str:
    cleaned = " ".join((text or "").strip().split())
    cleaned = re.sub(r"^[\W_]+", "", cleaned)
    if not cleaned:
        return "Conversation"
    cleaned = cleaned[:120].rstrip(" .,!?:;")
    return cleaned[:1].upper() + cleaned[1:]


def _extract_urls(*parts: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for raw in _URL_RE.findall(part or ""):
            url = raw.rstrip(".,;:!?\")'")
            if url and url not in seen:
                seen.add(url)
                out.append(url)
    return out


def _extract_tags(*parts: str, max_tags: int = 12) -> list[str]:
    freq: dict[str, int] = {}
    for part in parts:
        for w in _WORD_RE.findall((part or "").lower()):
            if w in _STOP_WORDS:
                continue
            freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:max_tags]]


def _find_okf_anchor_id(store: BrainStore) -> str | None:
    # Preferred explicit anchors first.
    for candidate in ("omni-okf-knowledge", "okf-knowledge", "okf"):
        try:
            store.get_skill(candidate)
            return candidate
        except (KeyError, FileNotFoundError):
            pass

    # Fallback: any non-conversation hit containing okf.
    for hit in store.search("okf", limit=20):
        if hit.get("category") != "conversation":
            return hit["id"]
    return None


def _log_conversation_turn(store: BrainStore, turn: dict[str, Any]) -> None:
    user_text = (turn.get("user_text") or "").strip()
    assistant_text = (turn.get("assistant_text") or "").strip()
    tool_results = turn.get("tool_results") or []
    tool_blob = "\n\n".join(
        "tool: " + str(t.get("name", "")) + "\n" + str(t.get("result", ""))
        for t in tool_results
    )

    if not user_text and not assistant_text:
        return

    subject = _normalize_subject(user_text)
    links = _extract_urls(user_text, assistant_text, tool_blob)
    tags = _extract_tags(user_text, assistant_text, tool_blob)
    okf_anchor_id = _find_okf_anchor_id(store)
    branch_id = store.ensure_branch(
        CONVERSATIONS_ROOT, "Conversations", "conversation",
        "Trunk node every recorded chat turn branches from.",
    )
    summary = assistant_text[:1800] if assistant_text else "(no assistant text)"
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    title = f"Conversation: {subject}"

    sources = [Source(url=u, quote="link captured from this conversation") for u in links]
    skill = Skill(
        id=f"conversation-{time.strftime('%Y%m%d%H%M%S', time.gmtime())}-{abs(hash(user_text)) % 10000:04d}",
        title=title,
        category="conversation",
        tags=tags,
        problem=user_text,
        solution=summary,
        code_examples="",
        best_practices=f"Recorded automatically from chat turn on {now}.",
        edge_cases=("tool outputs included" if tool_results else ""),
        sources=sources,
        # Branch from the conversations trunk (and the OKF anchor when there
        # is one), so a recorded turn grows a limb instead of adding another
        # spoke around the centre.
        related=[branch_id] + ([okf_anchor_id] if okf_anchor_id else []),
    )
    saved = store.add_skill(skill)
    # One event, carrying the whole path the charge should travel:
    # coordinator -> conversations branch -> the new atom. The viz animates
    # the hops in order, so you watch the brain reach out and grow the node
    # rather than everything flashing at once.
    bus.publish({
        "kind": "conversation_logged",
        "id": saved.id,
        "label": saved.title,
        "branch": branch_id,
        "path": [COORDINATOR_ID, branch_id, saved.id],
        "route": branch_id,
    })


def _workbench_root(store) -> Path:
    """Imported lazily at call time: workbench imports agent.runtimes, which
    imports agent.sandbox — pulling that chain in at module scope would make
    the server import order fragile for no benefit."""
    from ..workbench import workbench_root
    return workbench_root(store)


def _safe_path(root: Path, rel: str) -> Path:
    """Resolve a client-supplied relative path under `root`, rejecting any
    `..`/absolute-path escape — the file editor is scoped to one project
    directory, not the whole filesystem."""
    candidate = (root / rel).resolve() if rel else root.resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise HTTPException(400, f"path escapes the project directory: {rel!r}")
    return candidate


def _safe_dest(root: Path, rel: str) -> Path:
    """Resolve a *destination* under `root`.

    `_safe_path` is for things that already exist; a move or copy target
    usually doesn't. The distinction matters because an unchecked
    destination is the hole that makes an otherwise-guarded API able to
    write anywhere on the disk: `rename(src="a.txt", dest="../../evil")`.
    """
    if not (rel or "").strip():
        raise HTTPException(400, "destination path is required")
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise HTTPException(400, f"destination escapes the project directory: {rel!r}")
    return candidate


def _guard_deletion(root: Path, target: Path, recursive: bool, allow_top_level: bool) -> None:
    """Three graded protections, because the cost of each mistake differs.

    1. The project root itself is never deletable — no flag, no override.
    2. A **top-level folder** (a direct child of the root, e.g. src/ or
       tests/) needs an explicit acknowledgement. These are the structural
       ones, where a mis-click is the expensive kind.
    3. Any non-empty folder needs the recursive flag, so "delete" can never
       quietly take a tree with it.
    """
    root_resolved = root.resolve()
    if target == root_resolved:
        raise HTTPException(400, "refusing to delete the project root")

    if target.is_dir():
        is_top_level = target.parent == root_resolved
        if is_top_level and not allow_top_level:
            raise HTTPException(
                409,
                f"{target.name!r} is a top-level project folder — "
                "confirm explicitly to delete it",
            )
        if any(target.iterdir()) and not recursive:
            count = sum(1 for _ in target.rglob("*"))
            raise HTTPException(
                409, f"{target.name!r} is not empty ({count} items) — recursive delete not confirmed"
            )


class FileWrite(BaseModel):
    path: str
    content: str


class FsTarget(BaseModel):
    path: str
    recursive: bool = False
    allow_top_level: bool = False


class FsMove(BaseModel):
    path: str
    dest: str
    overwrite: bool = False


class FsNew(BaseModel):
    path: str          # parent directory, "" for the project root
    name: str


class SourceIn(BaseModel):
    url: str
    quote: str


class ProviderIn(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str | None = None  # None = leave existing key untouched; "" = clear it


class RunIn(BaseModel):
    language: str
    code: str


class SnippetIn(BaseModel):
    language: str
    code: str


class ImportExternalIn(BaseModel):
    source: str | None = None  # defaults to ./external_skills


class SettingIn(BaseModel):
    key: str
    value: str


class NodeIn(BaseModel):
    title: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    problem: str | None = None
    solution: str | None = None
    code_examples: str | None = None
    best_practices: str | None = None
    edge_cases: str | None = None
    sources: list[SourceIn] | None = None
    related: list[str] | None = None
    tool_generated: str | None = None


def _skill_to_json(skill: Skill) -> dict[str, Any]:
    return {
        "id": skill.id,
        "title": skill.title,
        "category": skill.category,
        "tags": skill.tags,
        "problem": skill.problem,
        "solution": skill.solution,
        "code_examples": skill.code_examples,
        "best_practices": skill.best_practices,
        "edge_cases": skill.edge_cases,
        "sources": [{"url": s.url, "quote": s.quote} for s in skill.sources],
        "related": skill.related,
        "tool_generated": skill.tool_generated,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
        "usage_count": skill.usage_count,
    }


def create_app(home: Path | str, project_dir: Path | str | None = None, llm: Any | None = None) -> FastAPI:
    """`llm` lets callers (tests, or an embedding process that already built
    one) inject an LLM for /ws/chat instead of the endpoint lazily
    constructing a real AnthropicLLM() — which needs ANTHROPIC_API_KEY and
    would otherwise make every chat test require a real key."""
    store = BrainStore(home)
    app = FastAPI(title="DariusAI Neural Network", version=__version__)
    app.state.store = store
    saved_project_dir = store.get_setting("project_dir", "") or ""
    if project_dir is not None:
        resolved_project_dir = Path(project_dir).expanduser().resolve()
    elif saved_project_dir and Path(saved_project_dir).expanduser().is_dir():
        resolved_project_dir = Path(saved_project_dir).expanduser().resolve()
    else:
        # Falling back to cwd meant the editor opened DariusAI's own
        # source tree — the one place a user's projects must not be.
        from ..workbench import workbench_root
        fallback = workbench_root(store)
        fallback.mkdir(parents=True, exist_ok=True)
        resolved_project_dir = fallback.resolve()
    app.state.project_dir = resolved_project_dir
    store.set_setting("project_dir", str(app.state.project_dir))
    app.state.llm = llm
    app.mount("/vendor", StaticFiles(directory=STATIC_DIR / "vendor"), name="vendor")

    @app.get("/")
    def index():
        # no-store, because a cached copy of this page is indistinguishable
        # from a broken app: every fix ships inside it, and WebView2 happily
        # serving yesterday's copy is why "nothing changed" after an update.
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store, must-revalidate"})

    @app.get("/favicon.png")
    def favicon():
        return FileResponse(STATIC_DIR / "favicon.png")

    @app.get("/api/health")
    def health():
        """Liveness probe — the launcher and any external watchdog need one
        cheap endpoint that touches nothing."""
        return {"status": "ok"}

    @app.get("/api/version")
    def get_version():
        """The page reads its version from here instead of hardcoding it in
        the HTML — the title bar and About box can't drift from the package.

        `page_build` is the mtime of the page the server would serve right
        now. The open page polls it and compares against the build it was
        itself loaded from, so a window left open across an update can say
        so instead of quietly showing a stale UI and looking broken."""
        page = STATIC_DIR / "index.html"
        return {
            "version": __version__,
            "display": VERSION_DISPLAY,
            "page_build": int(page.stat().st_mtime) if page.exists() else 0,
        }

    @app.get("/api/graph")
    def get_graph():
        return store.to_graph_payload()

    @app.get("/api/search")
    def search(q: str = "", limit: int = 10):
        return store.search(q, limit=limit)

    @app.get("/api/node/{node_id}")
    def get_node(node_id: str):
        if node_id == COORDINATOR_ID:
            raise HTTPException(400, "the coordinator node has no editable content")
        try:
            skill = store.get_skill(node_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return _skill_to_json(skill)

    @app.put("/api/node/{node_id}")
    def put_node(node_id: str, body: NodeIn):
        try:
            existing = store.get_skill(node_id)
        except KeyError:
            existing = Skill(id=node_id, title=body.title or node_id)

        if body.title is not None:
            existing.title = body.title
        if body.category is not None:
            existing.category = body.category
        if body.tags is not None:
            existing.tags = body.tags
        if body.problem is not None:
            existing.problem = body.problem
        if body.solution is not None:
            existing.solution = body.solution
        if body.code_examples is not None:
            existing.code_examples = body.code_examples
        if body.best_practices is not None:
            existing.best_practices = body.best_practices
        if body.edge_cases is not None:
            existing.edge_cases = body.edge_cases
        if body.sources is not None:
            existing.sources = [Source(url=s.url, quote=s.quote) for s in body.sources]
        if body.related is not None:
            existing.related = body.related
        if body.tool_generated is not None:
            existing.tool_generated = body.tool_generated

        saved = store.add_skill(existing)
        bus.publish({"kind": "node_edited", "id": saved.id, "route": COORDINATOR_ID, "label": saved.title})
        return _skill_to_json(saved)

    @app.delete("/api/node/{node_id}")
    def delete_node(node_id: str):
        if node_id == COORDINATOR_ID:
            raise HTTPException(400, "cannot delete the coordinator node")
        store.delete(node_id)
        bus.publish({"kind": "node_deleted", "id": node_id})
        return {"id": node_id, "status": "deleted"}

    @app.put("/api/project-dir")
    def set_project_dir(body: dict[str, str]):
        candidate = Path(body.get("path", ""))
        if not candidate.is_dir():
            raise HTTPException(400, f"not a directory: {candidate}")
        app.state.project_dir = candidate.resolve()
        store.set_setting("project_dir", str(app.state.project_dir))
        return {"project_dir": str(app.state.project_dir)}

    @app.get("/api/project-dir")
    def get_project_dir():
        return {"project_dir": str(app.state.project_dir)}

    @app.get("/api/settings")
    def get_settings():
        return store.all_settings()

    @app.put("/api/settings")
    def put_setting(body: SettingIn):
        store.set_setting(body.key, body.value)
        return {"key": body.key, "value": body.value}

    @app.get("/api/providers")
    def list_providers():
        return store.list_providers()

    @app.put("/api/providers/{name}")
    def upsert_provider(name: str, body: ProviderIn):
        # Never returns the plaintext key — upsert_provider's result is
        # already masked (BrainStore._provider_row_to_dict).
        return store.upsert_provider(name, base_url=body.base_url, model=body.model, api_key=body.api_key)

    @app.delete("/api/providers/{name}")
    def delete_provider(name: str):
        store.delete_provider(name)
        return {"name": name, "status": "deleted"}

    @app.put("/api/providers/{name}/activate")
    def activate_provider(name: str):
        try:
            store.set_active_provider(name)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return store.get_provider(name)

    @app.get("/api/start-with-windows")
    def get_start_with_windows():
        from ..os_integration import is_enabled
        return {"enabled": is_enabled()}

    @app.put("/api/start-with-windows")
    def put_start_with_windows(body: dict[str, bool]):
        from ..os_integration import set_enabled
        return {"enabled": set_enabled(bool(body.get("enabled")))}

    @app.get("/api/runtimes")
    def list_runtimes():
        """What this machine can actually run. The page uses it to decide
        which code blocks get a Run button — offering one that then fails is
        worse than not offering it."""
        from ..agent.runtimes import NOT_EXECUTABLE, detect
        return {"runtimes": detect(), "not_executable": sorted(NOT_EXECUTABLE)}

    @app.post("/api/run")
    def run_snippet_endpoint(body: RunIn):
        """Execute a code block. POST, never automatic, and only ever from
        an explicit click — this runs model-written code. It runs inside the
        project sandbox and returns the exact command it used."""
        from ..agent.runtimes import run_snippet
        from ..agent.sandbox import SandboxViolation

        try:
            return run_snippet(Sandbox(root=app.state.project_dir), body.language, body.code)
        except SandboxViolation as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/snippet")
    def save_snippet(body: SnippetIn):
        """Write a code block to the project's scratch directory so the
        editor can open it as a real, saveable, runnable file."""
        from ..agent.runtimes import write_snippet
        path = write_snippet(Path(app.state.project_dir), body.language, body.code)
        rel = path.relative_to(Path(app.state.project_dir).resolve()) if path.is_absolute() else path
        return {"path": str(rel).replace("\\", "/"), "absolute": str(path)}

    @app.get("/api/provider-presets")
    def provider_presets():
        from ..agent.model_catalog import presets
        return presets()

    @app.get("/api/providers/{name}/models")
    def provider_models(name: str):
        """The model field is a picklist, not a text box — this is where it
        gets filled from. Uses the key already saved for the provider, so
        the page never has to hold a key to discover models."""
        from ..agent.model_catalog import fallback_result, fetch_models, spec_for

        provider = store.get_provider(name)
        if provider is None:
            raise HTTPException(404, f"no provider named {name!r}")
        api_key = store.get_provider_api_key(name) if provider["has_api_key"] else ""
        try:
            return fetch_models(name, base_url=provider["base_url"], api_key=api_key)
        except Exception as exc:  # network down, bad key, no /models, provider outage
            # A provider with documented model ids stays usable even when its
            # catalogue can't be reached — falling back beats a dead end.
            if spec_for(name).fallback_models:
                return fallback_result(name, reason=str(exc))
            if isinstance(exc, ValueError):
                raise HTTPException(400, str(exc)) from exc
            raise HTTPException(502, f"could not reach {name}: {exc}") from exc

    @app.post("/api/shortcuts")
    def post_shortcuts():
        """Explicit, user-initiated only — this writes files to the desktop
        and Start Menu, so it's a POST behind a button, never something the
        page does on load."""
        from .. import os_integration
        try:
            return {"created": os_integration.install_shortcuts()}
        except OSError as exc:
            raise HTTPException(500, str(exc)) from exc

    @app.post("/api/import-external")
    def import_external_endpoint(body: ImportExternalIn = ImportExternalIn()):
        """Scan `external_skills/` and import every SKILL.md into the brain.

        Body is optional: when omitted, the folder location defaults to the
        project's `external_skills/` (next to the addon tree). The new nodes
        show up on the next /api/graph fetch and the brain's growth event
        fires so the viz can animate them in."""
        from ..brain.omni_import import import_external
        try:
            result = import_external(store, body.source)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        bus.publish({"kind": "external_imported", "count": result["imported"]})
        return result

    @app.get("/api/files")
    def list_files(path: str = ""):
        target = _safe_path(app.state.project_dir, path)
        if not target.is_dir():
            raise HTTPException(404, f"not a directory: {path!r}")
        entries = []
        for e in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            rel = str(e.relative_to(app.state.project_dir)).replace("\\", "/")
            entries.append({"name": e.name, "path": rel, "is_dir": e.is_dir()})
        return {"root": str(app.state.project_dir), "path": path, "entries": entries}

    @app.get("/api/file")
    def read_file(path: str):
        target = _safe_path(app.state.project_dir, path)
        if not target.is_file():
            raise HTTPException(404, f"not a file: {path!r}")
        data = target.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{path} is {len(data)} bytes, over the {MAX_FILE_BYTES}-byte edit limit")
        return {"path": path, "content": data.decode("utf-8", errors="replace")}

    @app.put("/api/file")
    def write_file(body: FileWrite):
        target = _safe_path(app.state.project_dir, body.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.content, encoding="utf-8")
        bus.publish({"kind": "file_saved", "path": body.path, "route": COORDINATOR_ID})
        return {"path": body.path, "status": "saved", "bytes": len(body.content.encode("utf-8"))}

    @app.get("/api/workbench")
    def workbench_info():
        """Where projects live, and which types this machine can build.

        `available` comes from real runtime detection, so the form can mark a
        type it can't set up instead of failing halfway through creation."""
        from ..agent.runtimes import detect
        from ..workbench import TEMPLATES, workbench_root

        runtimes = detect()
        root = workbench_root(store)
        return {
            "root": str(root),
            "exists": root.is_dir(),
            "projects": sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else [],
            "types": [
                {
                    "id": t.id,
                    "label": t.label,
                    "description": t.description,
                    "keywords": list(t.keywords),
                    "runtime": t.runtime,
                    "needs_env": t.needs_env,
                    "sqlite": t.sqlite,
                    "steps": [label for label, _ in t.steps],
                    "available": True if t.runtime is None else bool(runtimes.get(t.runtime, {}).get("available")),
                    "version": runtimes.get(t.runtime, {}).get("version", "") if t.runtime else "",
                }
                for t in TEMPLATES
            ],
        }

    # ---- filesystem operations -------------------------------------------
    # Every path here — source and destination — goes through the guards
    # above. This is the app's first destructive file API, so the traversal
    # tests were written before the happy-path ones.

    def _rel(target: Path) -> str:
        return str(target.relative_to(Path(app.state.project_dir).resolve())).replace("\\", "/")

    @app.delete("/api/fs")
    def fs_delete(body: FsTarget):
        import shutil
        root = Path(app.state.project_dir)
        target = _safe_path(root, body.path)
        if not target.exists():
            raise HTTPException(404, f"no such path: {body.path!r}")
        _guard_deletion(root, target, body.recursive, body.allow_top_level)

        was_dir = target.is_dir()
        if was_dir:
            shutil.rmtree(target)
        else:
            target.unlink()
        bus.publish({"kind": "file_deleted", "path": body.path, "route": COORDINATOR_ID})
        return {"path": body.path, "status": "deleted", "was_dir": was_dir}

    @app.post("/api/fs/rename")
    def fs_rename(body: FsMove):
        """Rename and move are the same operation — moving is renaming into
        a different parent — so one endpoint serves both."""
        root = Path(app.state.project_dir)
        source = _safe_path(root, body.path)
        dest = _safe_dest(root, body.dest)
        if not source.exists():
            raise HTTPException(404, f"no such path: {body.path!r}")
        if dest.exists() and not body.overwrite:
            raise HTTPException(409, f"{body.dest!r} already exists")
        if source == root.resolve():
            raise HTTPException(400, "refusing to move the project root")
        if source in dest.parents:
            raise HTTPException(400, "cannot move a folder inside itself")

        dest.parent.mkdir(parents=True, exist_ok=True)
        source.replace(dest)
        return {"path": body.path, "dest": _rel(dest), "status": "moved"}

    @app.post("/api/fs/copy")
    def fs_copy(body: FsMove):
        import shutil
        root = Path(app.state.project_dir)
        source = _safe_path(root, body.path)
        dest = _safe_dest(root, body.dest)
        if not source.exists():
            raise HTTPException(404, f"no such path: {body.path!r}")
        if dest.exists() and not body.overwrite:
            raise HTTPException(409, f"{body.dest!r} already exists")
        if source.is_dir() and source in dest.parents:
            raise HTTPException(400, "cannot copy a folder into itself")

        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, dest, dirs_exist_ok=body.overwrite)
        else:
            shutil.copy2(source, dest)
        return {"path": body.path, "dest": _rel(dest), "status": "copied"}

    @app.post("/api/fs/mkdir")
    def fs_mkdir(body: FsNew):
        root = Path(app.state.project_dir)
        parent = _safe_path(root, body.path)
        target = _safe_dest(root, (body.path + "/" if body.path else "") + body.name)
        if not parent.is_dir():
            raise HTTPException(400, f"not a directory: {body.path!r}")
        if target.exists():
            raise HTTPException(409, f"{body.name!r} already exists")
        target.mkdir(parents=True)
        return {"path": _rel(target), "status": "created", "is_dir": True}

    @app.post("/api/fs/new-file")
    def fs_new_file(body: FsNew):
        root = Path(app.state.project_dir)
        parent = _safe_path(root, body.path)
        target = _safe_dest(root, (body.path + "/" if body.path else "") + body.name)
        if not parent.is_dir():
            raise HTTPException(400, f"not a directory: {body.path!r}")
        if target.exists():
            raise HTTPException(409, f"{body.name!r} already exists")
        target.write_text("", encoding="utf-8")
        return {"path": _rel(target), "status": "created", "is_dir": False}

    @app.websocket("/ws/workbench")
    async def ws_workbench(ws: WebSocket):
        """Create a project, streaming setup output to the form's console.

        A websocket rather than a POST because `npm install` and `python -m
        venv` take tens of seconds, and a console that only prints once the
        request returns is indistinguishable from one that has hung."""
        await ws.accept()
        loop = asyncio.get_event_loop()
        try:
            request = await ws.receive_json()
        except Exception:
            await ws.close()
            return

        queue: asyncio.Queue = asyncio.Queue()

        def emit(event: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def work() -> None:
            from ..workbench import InvalidProjectName, ProjectExists, create_project
            try:
                result = create_project(
                    request.get("name", ""), request.get("template", ""), emit, store=store,
                )
                emit({"type": "done", **result})
            except (ProjectExists, InvalidProjectName, ValueError) as exc:
                emit({"type": "done", "ok": False, "error": str(exc)})
            except Exception as exc:  # never leave the console hanging
                emit({"type": "done", "ok": False, "error": f"{type(exc).__name__}: {exc}"})

        task = loop.run_in_executor(None, work)
        try:
            while True:
                event = await queue.get()
                await ws.send_json(event)
                if event.get("type") == "done":
                    break
            await task
        except WebSocketDisconnect:
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket):
        """One ChatSession per connection — a fresh conversation each time
        the chat panel/tab opens, same as the terminal REPL. Tool-calling
        turns run in a thread (session.send is synchronous, and a real
        AnthropicLLM call is blocking I/O); on_event pushes through a queue
        so intermediate tool activity streams to the client as it happens,
        not just the final reply."""
        await ws.accept()
        llm = app.state.llm
        if llm is None:
            try:
                from ..agent.llm import build_llm
                llm = build_llm(store)
            except Exception as exc:
                await ws.send_json({"type": "error", "message": f"LLM not available: {exc}"})
                await ws.close()
                return

        session = ChatSession(
            llm=llm,
            # The workbench, not just the open folder — the agent has to be
            # able to see sibling projects and create new ones for the
            # "all in one" workflow to mean anything. Still confined: an
            # unrelated folder opened from elsewhere stays the only root.
            tools=build_tool_registry(
                store, Sandbox.for_workspace(app.state.project_dir, _workbench_root(store))),
            on_turn_complete=lambda turn: _log_conversation_turn(store, turn),
        )
        loop = asyncio.get_event_loop()
        try:
            while True:
                raw = await ws.receive_text()
                # The UI's [⚡ Compact] button (and the auto-compaction
                # hook) sends `{"type": "compact"}` over the same socket;
                # intercept it before treating the payload as a user
                # prompt — running it through `session.send()` would
                # dump a JSON object into the model's context.
                parsed: dict[str, Any] | None = None
                try:
                    p = json.loads(raw)
                    if isinstance(p, dict):
                        parsed = p
                except (ValueError, TypeError):
                    # Not JSON, or not a dict — treat as plain user text.
                    pass

                if isinstance(parsed, dict) and parsed.get("type") == "compact":
                    result = await asyncio.to_thread(session.compact, force=True)
                    await ws.send_json({"type": "context_compacted", **result})
                    continue

                # Slash commands: typed-intercept. The chat input parses
                # leading `/` and POSTs a typed WS message here or sends raw
                # text like "/skills" or "/help".
                is_cmd = False
                cmd_name = ""
                cmd_args = []
                cmd_req_id = ""

                if isinstance(parsed, dict) and parsed.get("type") == "command":
                    is_cmd = True
                    cmd_name = str(parsed.get("name", ""))
                    cmd_args = list(parsed.get("args") or [])
                    cmd_req_id = str(parsed.get("request_id", ""))
                elif isinstance(raw, str) and raw.strip().startswith("/"):
                    parts = raw.strip().split()
                    if parts:
                        is_cmd = True
                        cmd_name = parts[0].lstrip("/")
                        cmd_args = parts[1:]
                        import time
                        cmd_req_id = f"cmd-{int(time.time()*1000)}"

                if is_cmd and cmd_name:
                    from ..agent.commands import (
                        CommandContext,
                        run_command as _run_command,
                    )
                    cmd_ctx = CommandContext(
                        store=store,
                        app_state=app.state,
                        request_id=cmd_req_id,
                        emit_log=lambda ev: ws.send_json(ev),
                    )
                    await _run_command(
                        ctx=cmd_ctx,
                        name=cmd_name,
                        args=cmd_args,
                        request_id=cmd_req_id,
                        ws_send=ws.send_json,
                    )
                    continue

                text = raw
                queue: asyncio.Queue = asyncio.Queue()

                def on_event(ev: dict[str, Any], queue: asyncio.Queue = queue) -> None:
                    loop.call_soon_threadsafe(queue.put_nowait, ev)

                async def run_send(text: str = text, queue: asyncio.Queue = queue) -> None:
                    try:
                        await asyncio.to_thread(session.send, text, on_event)
                    except Exception as exc:  # a bad key / network failure / tool bug must not hang the socket
                        loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(exc)})
                    finally:
                        loop.call_soon_threadsafe(queue.put_nowait, {"type": "_turn_done"})

                send_task = asyncio.create_task(run_send())
                while True:
                    ev = await queue.get()
                    if ev.get("type") == "_turn_done":
                        break
                    await ws.send_json(ev)
                await send_task
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        await ws.accept()
        for event in bus.recent():
            await ws.send_text(json.dumps({**event, "replay": True}))
        q = bus.subscribe()
        try:
            while True:
                event = await q.get()
                await ws.send_text(json.dumps(event))
        except WebSocketDisconnect:
            pass
        finally:
            bus.unsubscribe(q)

    return app
