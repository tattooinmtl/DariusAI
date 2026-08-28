"""The tool registry the Coder node calls into — real filesystem/shell/
research/brain access, described as Anthropic tool-use JSON schemas. Bound
to one BrainStore instance per registry (build_tool_registry(store)), so
learn_skill and search_brain write to and read from the same brain the
viz window is looking at.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..brain.learn import InsufficientResearchError, research, save_learned_skill
from ..brain.retrieval import DEFAULT_BUDGET_CHARS, DEFAULT_TOP_K, PassageIndex
from ..brain.skill import Skill, Source
from ..brain.store import COORDINATOR_ID, BrainStore
from ..events.bus import bus
from .sandbox import Sandbox

MAX_READ_BYTES = 200_000
MAX_SHELL_OUTPUT = 20_000

# A skill under this size is cheaper to hand over whole than to distil:
# the RAG detour costs a round trip, and at ~1,500 tokens the body is not
# what is filling the context window. Everything above it gets the
# passage treatment — which is most of the library, whose median file is
# ~7 KB and whose largest is over 100 KB.
SKILL_FULL_LOAD_BYTES = int(os.environ.get("DARIUSAI_SKILL_FULL_LOAD_BYTES", "6000"))


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., str]


@dataclass
class ToolRegistry:
    specs: dict[str, ToolSpec] = field(default_factory=dict)
    # Event publisher wired in by the consumer (chat session, langgraph
    # test, etc.). Tools that need to push UI state — like set_todos,
    # which drives the chat panel's phases panel — call this with a
    # dict the consumer knows how to forward. None by default; the
    # tool-side branch checks before firing so calling set_todos in a
    # unit test (no publisher) is a no-op rather than a crash.
    on_event: Callable[[dict[str, Any]], None] | None = None
    # The sandbox the tools were built against. Exposed so the chat
    # session can clear external-read grants at the top of each user
    # turn — the "one grant per prompt" contract lives at the session
    # boundary, not inside the sandbox's own state management.
    sandbox: "Sandbox | None" = None

    def publish(self, event: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(event)

    def register(self, spec: ToolSpec) -> None:
        self.specs[spec.name] = spec

    @staticmethod
    def node_id_for_tool(name: str) -> str:
        return f"tool-{name}"

    def to_anthropic_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "description": s.description, "input_schema": s.input_schema}
            for s in self.specs.values()
        ]

    def call(self, name: str, args: dict[str, Any]) -> str:
        spec = self.specs.get(name)
        if not spec:
            return f"ERROR: unknown tool {name!r}"
        route = self.node_id_for_tool(name)
        try:
            result = spec.fn(**args)
        except Exception as exc:  # a tool failure is data the model should see, not a crash
            bus.publish({"kind": "tool_call", "tool": name, "ok": False, "route": route, "id": route})
            return f"ERROR: {exc}"
        bus.publish({"kind": "tool_call", "tool": name, "ok": True, "route": route, "id": route})
        bus.publish({"kind": "node_used", "id": route, "label": spec.name, "route": route})
        return result


def _ensure_tool_node(store: BrainStore, spec: ToolSpec) -> None:
    node_id = ToolRegistry.node_id_for_tool(spec.name)
    try:
        existing = store.get_skill(node_id)
    except (KeyError, FileNotFoundError):
        existing = None

    title = spec.name.replace("_", " ").title()
    if existing is None:
        store.add_skill(Skill(
            id=node_id,
            title=title,
            category="tool",
            tags=["tool", spec.name],
            problem=f"Run tool `{spec.name}` from agent reasoning.",
            solution=spec.description,
            best_practices="Used automatically by tool-calling chat turns.",
        ))
        return

    changed = False
    if existing.category != "tool":
        existing.category = "tool"
        changed = True
    if existing.title != title:
        existing.title = title
        changed = True
    if spec.description and existing.solution != spec.description:
        existing.solution = spec.description
        changed = True
    if spec.name not in existing.tags:
        existing.tags = sorted(set(existing.tags + ["tool", spec.name]))
        changed = True
    if changed:
        store.add_skill(existing)


def _register(reg: ToolRegistry, store: BrainStore, spec: ToolSpec) -> None:
    reg.register(spec)
    _ensure_tool_node(store, spec)


# ---- tool implementations --------------------------------------------------

def _read_file(sandbox: Sandbox, path: str) -> str:
    p = sandbox.resolve(path)
    data = p.read_bytes()
    if len(data) > MAX_READ_BYTES:
        return f"ERROR: {path} is {len(data)} bytes, over the {MAX_READ_BYTES}-byte read limit"
    return data.decode("utf-8", errors="replace")


def _write_file(sandbox: Sandbox, path: str, content: str) -> str:
    # for_write=True refuses external grants — writes are never allowed
    # into a granted external tree, only the primary sandbox root.
    p = sandbox.resolve(path, for_write=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {path}"


def _request_external_read(sandbox: Sandbox, path: str, reason: str) -> str:
    """One-turn permission to read a directory outside the sandbox.

    The human is prompted with the path and the reason; on approval the
    directory + all descendants become readable for the rest of the turn
    only. Writes into the granted tree stay refused, and destructive
    commands (rm/del/mv/git-reset/…) are blocked even for reads.
    """
    _, message = sandbox.request_and_grant(path, reason)
    return message


def _list_dir(sandbox: Sandbox, path: str = ".") -> str:
    p = sandbox.resolve(path)
    entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    return "\n".join(("d " if e.is_dir() else "f ") + e.name for e in entries)


def _run_shell(sandbox: Sandbox, command: str, cwd: str | None = None, timeout: int = 60) -> str:
    """Execution goes through the sandbox: confined working directory,
    scrubbed environment, and a timeout that kills the whole process tree
    rather than just the shell that was launched."""
    return sandbox.run(command, cwd=cwd, timeout=timeout).to_text(MAX_SHELL_OUTPUT)


def _web_research(topic: str, min_results: int = 5) -> str:
    try:
        items = research(topic, min_results=min_results)
    except InsufficientResearchError as exc:
        return f"ERROR: {exc}"
    lines = []
    for it in items:
        lines.append(f"### {it.title}\nURL: {it.url}\n{it.text[:1500]}\n")
    return "\n".join(lines)


def _search_brain(store: BrainStore, query: str, limit: int = 10) -> str:
    hits = store.search(query, limit=limit)
    # Name the candidates. Without ids on the event the viz can only light
    # the coordinator, which is why every query used to flash the whole
    # graph regardless of what it was about.
    bus.publish({
        "kind": "brain_search", "query": query,
        "ids": [h["id"] for h in hits], "route": COORDINATOR_ID,
    })
    if not hits:
        return "no matching skills in the brain yet."
    return "\n".join(f"- {h['id']} [{h['category']}] {h['label']} (used {h['usage_count']}x)" for h in hits)


def _browse_brain(store: BrainStore, node_id: str = "") -> str:
    """The map, for when the agent doesn't know the vocabulary yet.

    search_brain answers "find me X" and needs a good guess at X. This answers
    "what do you have?", which is the question an agent actually starts with.
    Without it the library's own structure — the group branches the import
    builds — is invisible, and finding a skill means guessing keywords until
    one lands.

    Reads the graph rather than the index, so it is one hop over edges already
    in memory. Returns ids and labels only, exactly like search: browsing must
    stay cheap enough to do first.
    """
    if not node_id:
        children = store.graph.children_of(COORDINATOR_ID)
        header = "top of the brain — browse one of these, or search_brain for a specific topic:"
    else:
        if node_id not in store.graph.graph:
            return f"no node with id {node_id!r} — call browse_brain with no argument to see the top level."
        children = store.graph.children_of(node_id)
        header = f"under {node_id}:"

    if not children:
        return f"{node_id or 'the brain'} has nothing under it — load_skill on it to read it in full."

    lines = []
    for child in children:
        data = store.graph.graph.nodes[child]
        label = data.get("label", child)
        category = data.get("category", "")
        # Say when something is superseded, so the agent learns that from the
        # listing instead of by opening the outdated skill and acting on it.
        replacement = store.graph.superseded_by(child)
        suffix = f"  (superseded by {replacement})" if replacement else ""
        lines.append(f"- {child} [{category}] {label}{suffix}")
    return f"{header}\n" + "\n".join(lines)


def _load_skill(store: BrainStore, skill_id: str) -> str:
    """The other half of search_brain. Search returns labels only — cheap,
    and it has to be, because the brain grows without bound. This pulls one
    skill's full body in once the model has decided it's the relevant one,
    so context is spent on the skill actually being used instead of on
    every skill that might have been."""
    try:
        skill = store.get_skill(skill_id)
    except (KeyError, FileNotFoundError):
        return f"no skill with id {skill_id!r} — use search_brain to find the right id first."
    store.touch_usage(skill_id)  # usage drives node size in the viz and search ranking
    # The one node actually being read — this is the event that earns a bolt.
    bus.publish({
        "kind": "skill_used", "id": skill_id, "label": skill.title, "route": COORDINATOR_ID,
    })

    parts = [f"# {skill.title}  [{skill.category}]"]
    if skill.tags:
        parts.append("tags: " + ", ".join(skill.tags))
    for label, value in (
        ("Problem", skill.problem),
        ("Solution", skill.solution),
        ("Code examples", skill.code_examples),
        ("Best practices", skill.best_practices),
        ("Edge cases", skill.edge_cases),
    ):
        if value:
            parts.append(f"\n## {label}\n{value}")
    if skill.sources:
        parts.append("\n## Sources\n" + "\n".join(s.to_line() for s in skill.sources))
    return "\n".join(parts)


def _set_todos(reg: ToolRegistry, items: list[dict[str, str]]) -> str:
    """Update the active query's TodoList — the phases panel under
    the chat input. The agent calls this at the start of any
    multi-step task (per the doctrine: "If it has a checklist, create
    a todo per item") and again as phases move through pending →
    in_progress → done.

    Each item is `{id, title, status}` where status is one of
    `pending`, `in_progress`, `done`. The model is expected to keep
    ids stable across calls so the UI can animate transitions; new
    items get new ids. The full list is sent each call — partial
    updates are done by re-sending the whole list with the changed
    field. Empty list clears the panel.
    """
    if not isinstance(items, list):
        return "ERROR: items must be a list of {id, title, status} objects"
    valid_status = {"pending", "in_progress", "done"}
    for it in items:
        if not isinstance(it, dict):
            return f"ERROR: each item must be a dict, got {type(it).__name__}"
        if "id" not in it or "title" not in it or "status" not in it:
            return f"ERROR: each item needs id, title, status — got {it!r}"
        if it["status"] not in valid_status:
            return f"ERROR: status must be one of {valid_status}; got {it['status']!r}"
    reg.publish({"type": "todos", "items": items})
    return f"set {len(items)} todo(s)"


# ---- skill distillation (RAG over SKILL.md, instead of loading it whole) ---


def _skill_files(store: BrainStore) -> list[Path]:
    """Every SKILL.md the harness can invoke, from both libraries."""
    project_root = store.get_setting("project_dir", "")
    if not project_root:
        return []
    root = Path(project_root)
    return sorted(root.glob("addon/skills/*/*/SKILL.md")) + \
        sorted(root.glob("external_skills/*/*/SKILL.md"))


def _passage_index(store: BrainStore) -> PassageIndex:
    """One index per store, built lazily and cached on it — the chat
    session, the CLI and the websocket all share the store, so they share
    the index and its sync clock."""
    index = getattr(store, "_passage_index", None)
    if index is None:
        index = PassageIndex(store.conn)
        store._passage_index = index
    return index


def _sync_skill_index(store: BrainStore, force: bool = False) -> PassageIndex:
    """Bring the passage index up to date with the skill files on disk.

    Content-fingerprinted, so an unchanged library costs one read per file
    and no writes; rate-limited by `stale()` so a long tool-calling turn
    doesn't re-walk the tree on every iteration.
    """
    index = _passage_index(store)
    if index.enabled and (force or index.stale()):
        index.sync_paths(_skill_files(store))
    return index


def _touch_skill_node(store: BrainStore, name: str) -> None:
    """Best-effort usage bump + viz pulse for a skill read by name. The
    brain may not have imported the skill yet — that's fine, the file is
    the answer either way."""
    try:
        for h in store.search(name, limit=5):
            if h["label"] == name:
                store.touch_usage(h["id"])
                bus.publish({
                    "kind": "skill_used", "id": h["id"], "label": h["label"],
                    "route": COORDINATOR_ID,
                })
                return
    except Exception:
        pass


def _skill_lookup(store: BrainStore, query: str, k: int = DEFAULT_TOP_K, skill: str = "") -> str:
    """Retrieval, not loading: return the handful of paragraphs across the
    whole skill library that answer `query`.

    This is the step that replaces "invoke the skill and read all of it".
    The reply is capped at a few thousand characters no matter how many
    skills matched, and — unlike a loaded body — it is small enough to
    survive in the conversation for the rest of the turn without dominating
    every subsequent prompt.
    """
    if not query.strip():
        return "ERROR: empty query"
    index = _sync_skill_index(store)
    if not index.enabled:
        return ("passage index unavailable (SQLite built without FTS5) — "
                "fall back to search_brain + invoke_skill.")
    passages = index.search(query, k=k, budget_chars=DEFAULT_BUDGET_CHARS,
                            source=skill.split(":", 1)[-1].strip())
    if not passages:
        return (f"no passage matches {query!r}. Try browse_brain() for the group list, "
                f"or search_brain for skill ids.")

    seen: list[str] = []
    for p in passages:
        if p.source not in seen:
            seen.append(p.source)
            _touch_skill_node(store, p.source)
    header = (
        f"{len(passages)} passage(s) from {len(seen)} skill(s), distilled — not full bodies. "
        f"Call invoke_skill(name=<skill>, query=<what you need>) to go deeper in one skill, "
        f"or invoke_skill(name=<skill>, full=true) only if you truly need the whole checklist."
    )
    return header + "\n\n" + "\n\n".join(p.render() for p in passages)


def _resolve_skill_path(store: BrainStore, name: str) -> Path | str:
    """Locate a skill's SKILL.md, or return the error string to hand back.

    We don't know the group folder up front (depends on whether the skill
    came from addon/, external_skills/, or the user's own folders), so both
    roots are globbed.
    """
    bare = name.split(":", 1)[-1].strip()
    if not bare:
        return "ERROR: empty skill name"

    project_root = store.get_setting("project_dir", "")
    if not project_root:
        return "ERROR: no project_dir set — open the app once so the brain has a project to work on"
    root = Path(project_root)

    candidates = sorted(root.glob(f"addon/skills/*/{bare}/SKILL.md"))
    candidates += sorted(root.glob(f"external_skills/*/{bare}/SKILL.md"))
    if not candidates:
        return (
            f"no skill named {bare!r}. Use `browse_brain()` (no argument) for "
            f"the top-level groups, then call it with a group id to list its skills."
        )
    if len(candidates) > 1:
        names = ", ".join(f"{p.parent.parent.name}/{p.parent.name}" for p in candidates)
        return f"ambiguous name {bare!r} — found in: {names}. Use the full path."
    return candidates[0]


def _invoke_skill(store: BrainStore, name: str, query: str = "", full: bool = False) -> str:
    """The harness's equivalent of the runtime's `Skill` tool — but
    distilled by default.

    Small skills (under `SKILL_FULL_LOAD_BYTES`) come back whole: the RAG
    detour would cost more than it saves. Anything larger comes back as a
    heading outline plus the passages matching `query` (or the skill's own
    name, when no query is given), with `full=True` as the explicit opt-in
    for the entire body. The point is that the default path can't quietly
    drop 26,000 tokens into a conversation that then re-sends them on every
    tool iteration for the rest of the turn.

    Resolves `addon/skills/<group>/<name>/SKILL.md` under the project root —
    the source-of-truth content, not the parsed-fields view `load_skill`
    returns. Strips any `group:` prefix ('superpowers:brainstorming').
    """
    resolved = _resolve_skill_path(store, name)
    if isinstance(resolved, str):
        return resolved
    skill_md = resolved
    bare = skill_md.parent.name

    data = skill_md.read_text(encoding="utf-8")
    if len(data) > MAX_READ_BYTES:
        return f"ERROR: {skill_md} is {len(data)} bytes, over the {MAX_READ_BYTES}-byte read limit"

    _touch_skill_node(store, bare)

    if full or len(data) <= SKILL_FULL_LOAD_BYTES:
        return data

    index = _sync_skill_index(store)
    if not index.enabled:
        # No FTS5 to distil with. The old behaviour — whole body — beats
        # returning nothing.
        return data

    index.sync_file(skill_md, source=bare)
    passages = index.search(query or bare.replace("-", " "), k=DEFAULT_TOP_K,
                            budget_chars=DEFAULT_BUDGET_CHARS, source=bare)
    if not passages:
        passages = index.search(bare.replace("-", " "), k=2, source=bare)

    outline = index.outline(bare)
    parts = [
        f"# {bare} (distilled — {len(data):,} chars on disk, showing the relevant parts)",
    ]
    if outline:
        parts.append("Sections: " + " | ".join(outline[:25]))
    if passages:
        parts.append("\n" + "\n\n".join(p.render() for p in passages))
    parts.append(
        f"\nNeed a different section? invoke_skill(name={bare!r}, query='<the section or topic>'). "
        f"Need the whole checklist verbatim? invoke_skill(name={bare!r}, full=true)."
    )
    return "\n".join(parts)


def _list_projects(store: BrainStore) -> str:
    """The agent's view of the workbench. Without this it can only see the
    one folder it happens to be pointed at, which makes "carry on with the
    other project" impossible to answer."""
    from ..workbench import workbench_root
    root = workbench_root(store)
    if not root.is_dir():
        return f"no workbench yet at {root} — create a project to make one."
    projects = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not projects:
        return f"workbench {root} is empty — no projects yet."
    lines = [f"workbench: {root}"]
    for path in projects:
        marks = []
        if (path / ".venv").is_dir():
            marks.append("venv")
        if (path / "node_modules").is_dir():
            marks.append("node_modules")
        if list(path.glob("data/*.db")):
            marks.append("sqlite")
        lines.append(f"- {path.name}" + (f"  [{', '.join(marks)}]" if marks else ""))
    return "\n".join(lines)


def _project_types() -> str:
    from ..agent.runtimes import detect
    from ..workbench import TEMPLATES
    runtimes = detect()
    lines = []
    for template in TEMPLATES:
        available = True if template.runtime is None else runtimes.get(template.runtime, {}).get("available")
        lines.append(f"- {template.id}: {template.label}" + ("" if available else "  (runtime not installed)"))
    return "\n".join(lines)


def _create_project(store: BrainStore, name: str, project_type: str) -> str:
    """Same engine the New Project form drives — one implementation, so the
    agent and the user can't produce differently-shaped projects."""
    from ..workbench import InvalidProjectName, ProjectExists, create_project

    log: list[str] = []
    try:
        result = create_project(name, project_type, lambda ev: log.append(ev.get("line", "")) if ev.get("type") == "log" else None, store=store)
    except (ProjectExists, InvalidProjectName, ValueError) as exc:
        return f"ERROR: {exc}"
    tail = "\n".join(line for line in log if line)[-1500:]
    status = "created" if result["ok"] else "created files, but setup did not complete"
    return f"{status}: {result['path']}\n\n{tail}"


def _learn_skill(
    store: BrainStore,
    title: str,
    problem: str,
    solution: str,
    code_examples: str,
    best_practices: str,
    edge_cases: str,
    sources: list[dict[str, str]],
    category: str = "skill",
    tags: list[str] | None = None,
    related: list[str] | None = None,
    tool_generated: str | None = None,
) -> str:
    try:
        skill = save_learned_skill(
            store, title=title, problem=problem, solution=solution,
            code_examples=code_examples, best_practices=best_practices, edge_cases=edge_cases,
            sources=[Source(url=s["url"], quote=s["quote"]) for s in sources],
            category=category, tags=tags or [], related=related or [], tool_generated=tool_generated,
        )
    except InsufficientResearchError as exc:
        return f"ERROR: {exc}"
    return f"saved skill {skill.id!r} ({len(skill.sources)} sources) into the brain."


def build_tool_registry(store: BrainStore, sandbox: Sandbox | None = None, on_event: Callable[[dict[str, Any]], None] | None = None) -> ToolRegistry:
    """`sandbox` bounds every filesystem and shell tool. It defaults to
    Sandbox.unrestricted() only so existing callers keep working; every
    production entry point passes a real one rooted at the project
    directory. `on_event` is the chat-session's per-call event
    publisher — tools that push UI state (set_todos) call it. Pass
    None in unit tests; the tool will still return its result, just
    without the UI side-effect."""
    sandbox = sandbox or Sandbox.unrestricted()
    reg = ToolRegistry()
    reg.on_event = on_event
    reg.sandbox = sandbox
    _register(reg, store, ToolSpec(
        name="read_file",
        description="Read a text file's contents.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        fn=lambda path: _read_file(sandbox, path),
    ))
    _register(reg, store, ToolSpec(
        name="write_file",
        description="Write (create or overwrite) a text file.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        fn=lambda path, content: _write_file(sandbox, path, content),
    ))
    _register(reg, store, ToolSpec(
        name="list_dir",
        description="List a directory's immediate contents.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        fn=lambda path=".": _list_dir(sandbox, path),
    ))
    _register(reg, store, ToolSpec(
        name="request_external_read",
        description=(
            "Ask the human for one-turn permission to read a directory tree OUTSIDE "
            "the sandbox root — e.g. a reference project the user wants you to study "
            "before writing something compatible with it. Grants are: (1) exactly the "
            "folder given plus all subfolders, never a parent; (2) READ-ONLY — the "
            "file-write tool refuses paths under the grant, and destructive shell "
            "commands (rm, del, mv, rmdir, git reset --hard, git clean, output "
            "redirection into the tree, etc.) are refused too; (3) valid for THIS "
            "user turn only — the next user message clears every grant and you must "
            "ask again. Provide a clear `reason` — the human sees it in the prompt "
            "and it is what convinces them to approve."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "absolute path to the folder to read"},
                "reason": {"type": "string", "description": "one sentence: what you'll look for and why"},
            },
            "required": ["path", "reason"],
        },
        fn=lambda path, reason: _request_external_read(sandbox, path, reason),
    ))
    _register(reg, store, ToolSpec(
        name="run_shell",
        description="Run a shell command (any language's toolchain — python, node, go, cargo, etc.) and return its output.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout": {"type": "integer", "description": "seconds, default 60"},
            },
            "required": ["command"],
        },
        fn=lambda command, cwd=None, timeout=60: _run_shell(sandbox, command, cwd, timeout),
    ))
    _register(reg, store, ToolSpec(
        name="web_research",
        description=(
            "Search the web and fetch page text for a topic you don't already know how to handle. "
            "Use this BEFORE learn_skill — read what it returns, then cite the sources you actually used."
        ),
        input_schema={
            "type": "object",
            "properties": {"topic": {"type": "string"}, "min_results": {"type": "integer"}},
            "required": ["topic"],
        },
        fn=_web_research,
    ))
    _register(reg, store, ToolSpec(
        name="search_brain",
        description=(
            "Search previously learned skills/tools/patterns in the brain before researching from scratch. "
            "Returns ids and labels only — call load_skill with an id to read one."
        ),
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        fn=lambda query, limit=10: _search_brain(store, query, limit),
    ))
    _register(reg, store, ToolSpec(
        name="browse_brain",
        description=(
            "List what the brain holds, by structure rather than by keyword. Call with no argument for the "
            "top-level groups, then with a group id to see its skills. Use this when you don't yet know what "
            "the brain calls a thing — it is faster and surer than guessing search terms. "
            "Returns ids and labels only; call load_skill to read one."
        ),
        input_schema={"type": "object", "properties": {"node_id": {"type": "string"}}},
        fn=lambda node_id="": _browse_brain(store, node_id),
    ))
    _register(reg, store, ToolSpec(
        name="list_projects",
        description="List the projects in the DariusAI workbench, with what each already has set up "
                    "(venv, node_modules, sqlite). Use before assuming a project does or doesn't exist.",
        input_schema={"type": "object", "properties": {}},
        fn=lambda: _list_projects(store),
    ))
    _register(reg, store, ToolSpec(
        name="project_types",
        description="List the project types that can be created, and which are unavailable because "
                    "their runtime isn't installed on this machine.",
        input_schema={"type": "object", "properties": {}},
        fn=_project_types,
    ))
    _register(reg, store, ToolSpec(
        name="create_project",
        description="Create a new project in the workbench: scaffolds files, initialises SQLite where "
                    "the type uses it, creates a virtual environment and installs dependencies. "
                    "Call project_types first to choose a valid type.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "project name; becomes the folder name"},
                "project_type": {"type": "string", "description": "an id from project_types, e.g. 'python' or 'htmx'"},
            },
            "required": ["name", "project_type"],
        },
        fn=lambda name, project_type: _create_project(store, name, project_type),
    ))
    _register(reg, store, ToolSpec(
        name="load_skill",
        description=(
            "Read one skill from the brain in full, by id, after search_brain has identified it. "
            "This is how a language guide, review checklist or design standard gets loaded on demand "
            "instead of every skill being carried in context."
        ),
        input_schema={
            "type": "object",
            "properties": {"skill_id": {"type": "string"}},
            "required": ["skill_id"],
        },
        fn=lambda skill_id: _load_skill(store, skill_id),
    ))
    _register(reg, store, ToolSpec(
        name="set_todos",
        description=(
            "Update the active query's TodoList panel (the phases list under the chat input). "
            "Use at the start of any multi-step task with a checklist, and update as phases move "
            "through pending → in_progress → done. Each item is `{id, title, status}` where status "
            "is 'pending' | 'in_progress' | 'done'. Send the full list each call; partial updates are "
            "done by re-sending the whole list with the changed field. Empty list clears the panel. "
            "Stable ids across calls let the UI animate transitions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "title": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "done"]},
                        },
                        "required": ["id", "title", "status"],
                    },
                },
                "required": ["items"],
            },
        },
        fn=lambda items: _set_todos(reg, items),
    ))
    _register(reg, store, ToolSpec(
        name="invoke_skill",
        description=(
            "Load a skill by name (e.g. 'brainstorming', 'systematic-debugging') and return its full "
            "SKILL.md body so the model can follow its checklist. This is the harness's equivalent of "
            "the runtime's `Skill` tool, and the entry point for superpowers' methodology. Use this "
            "before any creative work or bug fix — the using-superpowers bootstrap says so. Accepts "
            "the name with or without a group prefix (e.g. 'brainstorming' or "
            "'superpowers:brainstorming'). Returns the relevant sections plus the skill's section "
            "list, not the whole file — pass `query` to steer which sections come back, and "
            "`full=true` only when you genuinely need the entire checklist verbatim."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "query": {"type": "string", "description": "what you need from the skill; steers which sections come back"},
                "full": {"type": "boolean", "description": "return the entire SKILL.md — expensive, use sparingly"},
            },
            "required": ["name"],
        },
        fn=lambda name, query="", full=False: _invoke_skill(store, name, query, full),
    ))
    _register(reg, store, ToolSpec(
        name="skill_lookup",
        description=(
            "Retrieve the specific paragraphs from the skill library that answer a question, without "
            "loading any skill in full. This is the cheap first move for 'how should I do X' — it "
            "searches inside every SKILL.md and returns a few matching passages with their skill name "
            "and heading. Follow up with invoke_skill(name, query=...) only if a passage shows you need "
            "more of that one skill."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "description": "max passages, default 4"},
                "skill": {"type": "string", "description": "restrict to one skill name"},
            },
            "required": ["query"],
        },
        fn=lambda query, k=DEFAULT_TOP_K, skill="": _skill_lookup(store, query, k, skill),
    ))
    _register(reg, store, ToolSpec(
        name="learn_skill",
        description=(
            "File a new skill node in the brain after researching something you didn't already know. "
            "Requires at least 5 cited sources (url + a real quote from each) spanning at least 3 "
            "distinct domains — call web_research first and read its output before calling this."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "category": {"type": "string", "enum": ["skill", "tool", "language", "framework", "pattern", "project", "preference"]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "problem": {"type": "string"},
                "solution": {"type": "string"},
                "code_examples": {"type": "string"},
                "best_practices": {"type": "string"},
                "edge_cases": {"type": "string"},
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}, "quote": {"type": "string"}},
                        "required": ["url", "quote"],
                    },
                    "minItems": 5,
                },
                "related": {"type": "array", "items": {"type": "string"}},
                "tool_generated": {"type": "string", "description": "code for a generated tool, if this skill produced one"},
            },
            "required": ["title", "problem", "solution", "code_examples", "best_practices", "edge_cases", "sources"],
        },
        fn=lambda **kwargs: _learn_skill(store, **kwargs),
    ))
    return reg
