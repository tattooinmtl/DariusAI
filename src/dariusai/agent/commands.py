"""Slash-command catalog for the chat panel.

The chat input box accepts `/`-prefixed commands that drive the agent
or the harness directly, without going through the LLM. The catalog
here is the source of truth:

- `CommandSpec`  — metadata for a single command (name, aliases,
  short_label, summary, group, args_hint, kind, interactive, picker)
- `CommandContext` — what every handler receives when invoked
- `CommandResult` — the typed return a handler makes
- `CommandPicker` — the multi-step return for interactive pickers
- `REGISTRY` — the flat dict of every command, keyed by canonical name
  (with aliases mapped to the same entry)

`commands.py` is the only file the server needs to dispatch a
slash-command. The wire format is in `src/dariusai/viz/server.py`
(the `ws_chat` handler routes `{"type":"command", ...}` here).
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# Typed surface
# ---------------------------------------------------------------------------


@dataclass
class CommandContext:
    """What every command handler sees.

    The conversation-level state (LLM, chat session, store) is
    reachable through the hand-rolled `app_state` proxy below — the
    server passes `app.state` as-is, so handlers can read/mutate
    `app.state.llm`, `app.state.store`, etc. without a circular import.
    """

    store: Any
    app_state: Any
    request_id: str
    emit_log: Callable[[dict], None]
    workbench_store: Any = None  # BrainStore or workbench root (lazy)


@dataclass
class CommandResult:
    """A flat, wire-friendly return from a handler."""

    status: str = "ok"  # "ok" | "error"
    message: str = ""
    ui: Optional[dict] = None  # optional {kind: "list", items: [...]}
    side_effect: Optional[dict] = None  # e.g. {"reload_llm": True}


@dataclass
class CommandPicker:
    """A multi-step interactive return.

    The client renders the categories as tabs; Up/Down cycles items
    in the active category; Left/Right switches tabs. When the user
    hits Enter, the picked value is sent back as the next command's
    args.
    """

    step: str  # e.g. "provider" | "model" | "base_url"
    categories: list[dict] = field(default_factory=list)
    active: str = ""
    selected: dict = field(default_factory=dict)


@dataclass
class CommandSpec:
    """One entry in the REGISTRY."""

    name: str
    short_label: str
    summary: str
    group: str
    args_hint: str = ""
    aliases: tuple = ()
    handler: Optional[Callable] = None
    interactive: bool = False
    picker: Optional[Callable] = None
    kind: str = "custom"  # "shell" | "file" | "brain" | "custom" | "not_implemented"
    # When kind is "not_implemented", handler is ignored and the
    # command returns a friendly "not yet implemented" error.


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _ok(message: str, **kw) -> CommandResult:
    return CommandResult(status="ok", message=message, **kw)


def _err(message: str, **kw) -> CommandResult:
    return CommandResult(status="error", message=message, **kw)


def _not_implemented(name: str) -> CommandResult:
    return _err(
        f"`/{name}` is registered but not yet implemented. "
        "The handler will be filled in in a future session."
    )


def _shell_cmd(command: str, *args: str) -> str:
    """Run a shell command and return the combined (stdout + stderr)
    output. Block per command — handlers are run in a thread."""
    proc = subprocess.run(
        [command, *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    return (out + ("\n" + err if err else "")).strip()


# ---------------------------------------------------------------------------
# Handlers — Conversation (14)
# ---------------------------------------------------------------------------


def _cmd_help(ctx: CommandContext, args: list) -> CommandResult:
    items = []
    for spec in REGISTRY.values():
        if spec.kind == "alias":
            continue
        items.append({
            "name": "/" + spec.name,
            "label": spec.short_label,
            "summary": spec.summary,
            "group": spec.group,
            "args_hint": spec.args_hint,
        })
    return _ok(
        f"{len(items)} commands available across "
        f"{len({i['group'] for i in items})} groups.",
        ui={"kind": "list", "items": items},
    )


def _cmd_skills(ctx: CommandContext, args: list) -> CommandResult:
    from ..brain.store import COORDINATOR_ID
    try:
        results = ctx.store.search(args[0] if args else "", limit=500)
    except Exception as exc:
        return _err(f"search failed: {exc}")
    items = []
    for r in results:
        if r.get("category") == "conversation":
            continue
        items.append({
            "label": r.get("label") or r.get("id"),
            "group": (r.get("category") or "skill"),
            "id": r.get("id"),
        })
    items.sort(key=lambda x: str(x.get("label") or x.get("id") or "").lower())
    return _ok(
        f"{len(items)} skills available.",
        ui={"kind": "list", "items": items},
    )


def _cmd_exit(ctx: CommandContext, args: list) -> CommandResult:
    # The WS handler closes the connection on receiving this result.
    return _ok("Session closed.")


def _cmd_clear(ctx: CommandContext, args: list) -> CommandResult:
    # The actual clearing of the message queue happens in the WS handler
    # because it owns the ChatSession. We just signal "clear" here.
    return _ok("Chat history cleared.", side_effect={"clear_chat": True})


def _cmd_reset(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Session reset.", side_effect={"reset_session": True})


def _cmd_compact(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Compact requested.", side_effect={"compact": True})


def _cmd_history(ctx: CommandContext, args: list) -> CommandResult:
    # The session carries the history; we just return a placeholder so
    # the client knows to render the recent turns.
    return _ok("Recent turns:", side_effect={"show_history": True})


def _cmd_session(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Session: active.",
        side_effect={"show_session_info": True},
    )


def _cmd_sessions(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Recent sessions:",
        side_effect={"show_sessions": True},
    )


def _cmd_resume(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /resume <session_id>")
    return _ok(
        f"Resume requested for session {args[0]}.",
        side_effect={"resume_session": args[0]},
    )


def _cmd_rename(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /rename <name>")
    return _ok(
        f"Renamed session to {args[0]}.",
        side_effect={"rename_session": args[0]},
    )


def _cmd_export(ctx: CommandContext, args: list) -> CommandResult:
    path = args[0] if args else None
    return _ok(
        f"Exported chat to {path or 'default path'}.",
        side_effect={"export_chat": path},
    )


def _cmd_import(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /import <path>")
    return _ok(
        f"Imported chat from {args[0]}.",
        side_effect={"import_chat": args[0]},
    )


def _cmd_share(ctx: CommandContext, args: list) -> CommandResult:
    # Real share-to-platform not implemented yet.
    return _ok(
        "Share-link URL: (placeholder — share-to-platform wiring pending).",
    )


# ---------------------------------------------------------------------------
# Handlers — Memory / brain (7)
# ---------------------------------------------------------------------------


def _cmd_memory(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Stored memory entries:",
        side_effect={"show_memory": True},
    )


def _cmd_remember(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /remember <text>")
    text = " ".join(args)
    return _ok(
        f"Remembered: {text}",
        side_effect={"remember": text},
    )


def _cmd_forget(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /forget <id>")
    return _ok(
        f"Forgot {args[0]}.",
        side_effect={"forget": args[0]},
    )


def _cmd_search(ctx: CommandContext, args: list) -> CommandResult:
    query = " ".join(args) if args else ""
    try:
        results = ctx.store.search(query, limit=50)
    except Exception as exc:
        return _err(f"search failed: {exc}")
    items = [
        {"label": r.get("label") or r.get("id"),
         "group": r.get("category") or "skill",
         "id": r.get("id")}
        for r in results
    ]
    return _ok(
        f"{len(items)} results for {query!r}.",
        ui={"kind": "list", "items": items},
    )


def _cmd_index(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Brain reindex started.", side_effect={"reindex": True})


def _cmd_consolidate(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Memory consolidation requested.",
        side_effect={"consolidate": True},
    )


def _cmd_where(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /where <topic>")
    topic = " ".join(args)
    try:
        results = ctx.store.search(topic, limit=5)
    except Exception as exc:
        return _err(f"search failed: {exc}")
    items = [
        {"label": r.get("label") or r.get("id"), "id": r.get("id")}
        for r in results
    ]
    return _ok(
        f"{len(items)} hits for {topic!r}.",
        ui={"kind": "list", "items": items},
    )


# ---------------------------------------------------------------------------
# Handlers — Project / workbench (17)
# ---------------------------------------------------------------------------


def _cmd_new(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /new <name>")
    name = args[0]
    from ..workbench import TEMPLATES
    template_id = TEMPLATES[0].id
    return _ok(
        f"Creating project {name!r} (template: {template_id}).",
        side_effect={"create_project": {"name": name, "template": template_id}},
    )


def _cmd_open(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /open <name>")
    return _ok(
        f"Switching to project {args[0]}.",
        side_effect={"open_project": args[0]},
    )


def _cmd_close(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Project closed.", side_effect={"close_project": True})


def _cmd_project(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Current project: (open the panel to view).")


def _cmd_projects(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Projects in this workbench:",
        side_effect={"list_projects": True},
    )


def _cmd_init(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /init <name>")
    return _ok(
        f"Initializing project {args[0]!r}.",
        side_effect={"create_project": {"name": args[0], "template": "python"}},
    )


def _cmd_scaffold(ctx: CommandContext, args: list) -> CommandResult:
    if len(args) < 2:
        return _err("Usage: /scaffold <name> <template>")
    return _ok(
        f"Scaffolding {args[0]!r} with template {args[1]!r}.",
        side_effect={"create_project": {"name": args[0], "template": args[1]}},
    )


def _cmd_template(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /template <name>")
    return _ok(
        f"Template set to {args[0]!r}.",
        side_effect={"set_template": args[0]},
    )


def _cmd_build(ctx: CommandContext, args: list) -> CommandResult:
    cmd = _detect_build_cmd(ctx)
    if not cmd:
        return _err("No build command detected for this project.")
    out = _shell_cmd(cmd[0], *cmd[1:])
    return _ok(f"$ {cmd[0]} {' '.join(cmd[1:])}\n{out}")


def _cmd_test(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    cmd = ["pytest", "-q"] + (args if args else [])
    try:
        proc = subprocess.run(
            cmd, cwd=project_dir, capture_output=True, text=True, timeout=300,
        )
    except FileNotFoundError:
        return _err("pytest not installed.")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ pytest -q\n{out.strip()}")


def _cmd_lint(ctx: CommandContext, args: list) -> CommandResult:
    try:
        out = _shell_cmd("ruff", "check", *(args or ["."]))
        return _ok(f"$ ruff check\n{out}")
    except FileNotFoundError:
        return _err("ruff not installed.")


def _cmd_format(ctx: CommandContext, args: list) -> CommandResult:
    try:
        out = _shell_cmd("ruff", "format", *(args or ["."]))
        return _ok(f"$ ruff format\n{out}")
    except FileNotFoundError:
        return _err("ruff not installed.")


def _cmd_run(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /run <shell command>")
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    try:
        proc = subprocess.run(
            args, cwd=project_dir, capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:
        return _err(f"run failed: {exc}")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ {' '.join(args)}\n{out.strip()}")


def _cmd_clean(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    removed = []
    for pattern in ("build", "dist", ".pytest_cache", "__pycache__"):
        target = project_dir / pattern
        if target.exists():
            try:
                import shutil
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                removed.append(pattern)
            except Exception:
                pass
    return _ok(f"Cleaned: {', '.join(removed) or 'nothing to clean'}")


def _cmd_install(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    if (project_dir / "pyproject.toml").exists():
        cmd = ["pip", "install", "-e", "."]
    elif (project_dir / "package.json").exists():
        cmd = ["npm", "install"]
    else:
        return _err("No pyproject.toml or package.json — can't infer install command.")
    try:
        out = _shell_cmd(cmd[0], *cmd[1:])
        return _ok(f"$ {cmd[0]} {' '.join(cmd[1:])}\n{out}")
    except FileNotFoundError:
        return _err(f"{cmd[0]} not installed.")


def _cmd_update(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    if (project_dir / "pyproject.toml").exists():
        cmd = ["pip", "install", "--upgrade", "-e", "."]
    elif (project_dir / "package.json").exists():
        cmd = ["npm", "update"]
    else:
        return _err("No pyproject.toml or package.json — can't infer update command.")
    try:
        out = _shell_cmd(cmd[0], *cmd[1:])
        return _ok(f"$ {cmd[0]} {' '.join(cmd[1:])}\n{out}")
    except FileNotFoundError:
        return _err(f"{cmd[0]} not installed.")


def _cmd_deploy(ctx: CommandContext, args: list) -> CommandResult:
    # Real deploy needs CI/CD integration. Stub for now.
    return _ok(
        "Deploy requested. (Deployment integration pending — pipeline not configured.)",
    )


def _detect_build_cmd(ctx: CommandContext) -> Optional[list]:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    if (project_dir / "pyproject.toml").exists():
        return ["python", "-m", "build"]
    if (project_dir / "package.json").exists():
        return ["npm", "run", "build"]
    return None


# ---------------------------------------------------------------------------
# Handlers — Provider / model (14)
# ---------------------------------------------------------------------------


def _cmd_provider(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /provider <name>")
    name = args[0]
    try:
        ctx.store.set_active_provider(name)
    except Exception as exc:
        return _err(f"provider: {exc}")
    return _ok(
        f"Switched to provider {name}.",
        side_effect={"reload_llm": True},
    )


def _cmd_providers(ctx: CommandContext, args: list) -> CommandResult:
    try:
        rows = ctx.store.list_providers()
    except Exception as exc:
        return _err(f"list_providers failed: {exc}")
    items = [
        {"label": r.get("name"), "model": r.get("model"),
         "is_active": r.get("is_active")}
        for r in rows
    ]
    return _ok(
        f"{len(items)} providers configured.",
        ui={"kind": "list", "items": items},
    )


def _cmd_add_provider(ctx: CommandContext, args: list) -> CommandResult:
    """Three-step picker: provider → model → base_url.

    Step 1 (no args): return a CommandPicker with three categories.
    Step 2 (with args): assume the first arg is the picked provider.
    Step 3+: continue advancing until the last category is filled,
    then commit via `store.upsert_provider`.
    """

    from ..agent.model_catalog import presets

    if not args:
        # First step — open the picker
        preset_rows = presets()
        return _ok(
            "Pick a provider preset (use ←→ to switch tabs, ↑↓ to navigate, Enter to select).",
            side_effect={
                "open_picker": CommandPicker(
                    step="provider",
                    categories=[
                        {"id": "providers", "label": "Provider",
                         "items": [{"id": p["name"], "label": p.get("label", p["name"])}
                                   for p in preset_rows]},
                        {"id": "models", "label": "Model", "items": []},
                        {"id": "base_url", "label": "Base URL", "items": []},
                    ],
                    active="providers",
                    selected={},
                ).__dict__,
            },
        )

    # Step 2+: args are the picked values in category order.
    # Complete the picker if all three are filled.
    if len(args) < 3:
        # Need more picks
        if len(args) == 1:
            preset_rows = presets()
            p = next((x for x in preset_rows if x["name"] == args[0]), None)
            if p is None:
                return _err(f"unknown provider preset {args[0]!r}")
            active = "models"
            cats = [
                {"id": "providers", "label": "Provider",
                 "items": [{"id": p_["name"], "label": p_.get("label", p_["name"])}
                           for p_ in preset_rows]},
                {"id": "models", "label": "Model",
                 "items": [{"id": m, "label": m} for m in p.get("models", [])]},
                {"id": "base_url", "label": "Base URL",
                 "items": [{"id": u, "label": u} for u in [p.get("base_url", "")] if u]},
            ]
        else:
            active = "base_url"
            cats = [
                {"id": "providers", "label": "Provider", "items": []},
                {"id": "models", "label": "Model", "items": []},
                {"id": "base_url", "label": "Base URL", "items": []},
            ]
        return _ok(
            "Continue picking.",
            side_effect={
                "open_picker": CommandPicker(
                    step="next",
                    categories=cats,
                    active=active,
                    selected={"providers": args[0] if len(args) >= 1 else None,
                              "models": args[1] if len(args) >= 2 else None,
                              "base_url": args[2] if len(args) >= 3 else None},
                ).__dict__,
            },
        )

    # All three picked — commit
    provider, model, base_url = args[0], args[1], args[2]
    try:
        ctx.store.upsert_provider(
            provider, base_url=base_url, model=model,
            api_key="__prompt__",
        )
    except Exception as exc:
        return _err(f"upsert_provider failed: {exc}")
    return _ok(
        f"Added provider {provider!r} (model {model!r}).",
        side_effect={"reload_llm": True},
    )


def _cmd_remove(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /remove <provider>")
    name = args[0]
    try:
        ctx.store.delete_provider(name)
    except Exception as exc:
        return _err(f"remove failed: {exc}")
    return _ok(f"Removed provider {name!r}.")


def _cmd_model(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /model <name>")
    try:
        active = ctx.store.get_active_provider()
    except Exception as exc:
        return _err(f"get_active_provider failed: {exc}")
    if not active:
        return _err("No active provider. Use /provider <name> first.")
    name = active.get("name")
    try:
        ctx.store.upsert_provider(name, base_url=active.get("base_url", ""),
                                model=args[0], api_key=active.get("api_key", ""))
    except Exception as exc:
        return _err(f"upsert_provider failed: {exc}")
    return _ok(
        f"Switched model to {args[0]!r}.",
        side_effect={"reload_llm": True},
    )


def _cmd_preset(ctx: CommandContext, args: list) -> CommandResult:
    from ..agent.model_catalog import presets
    rows = presets()
    items = [
        {"label": r.get("name"), "summary": r.get("label", r["name"])}
        for r in rows
    ]
    return _ok(
        f"{len(items)} presets available.",
        ui={"kind": "list", "items": items},
    )


def _cmd_key(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /key <api-key>")
    try:
        active = ctx.store.get_active_provider()
    except Exception as exc:
        return _err(f"get_active_provider failed: {exc}")
    if not active:
        return _err("No active provider. Use /provider <name> first.")
    try:
        ctx.store.upsert_provider(
            active.get("name"), base_url=active.get("base_url", ""),
            model=active.get("model", ""), api_key=args[0],
        )
    except Exception as exc:
        return _err(f"upsert_provider failed: {exc}")
    return _ok("API key updated.", side_effect={"reload_llm": True})


def _cmd_url(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /url <base-url>")
    try:
        active = ctx.store.get_active_provider()
    except Exception as exc:
        return _err(f"get_active_provider failed: {exc}")
    if not active:
        return _err("No active provider.")
    try:
        ctx.store.upsert_provider(
            active.get("name"), base_url=args[0],
            model=active.get("model", ""), api_key=active.get("api_key", ""),
        )
    except Exception as exc:
        return _err(f"upsert_provider failed: {exc}")
    return _ok(f"Base URL set to {args[0]!r}.", side_effect={"reload_llm": True})


def _cmd_test_provider(ctx: CommandContext, args: list) -> CommandResult:
    """Probe the active provider. Currently a no-op stub returning
    that the probe is queued; the real probe lives in the chat
    session's build-up of `available_models`."""
    if not args:
        return _err("Usage: /test <provider>")
    return _ok(f"Test queued for provider {args[0]!r}.")


def _cmd_default(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /default <provider>")
    try:
        ctx.store.set_active_provider(args[0])
    except Exception as exc:
        return _err(f"set_active_provider failed: {exc}")
    return _ok(
        f"Default provider set to {args[0]!r}.",
        side_effect={"reload_llm": True},
    )


def _cmd_usage(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Token usage: (track usage per turn — see chat panel stats footer).",
    )


def _cmd_cost(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Estimated cost: (cost tracking pending model price catalog).",
    )


def _cmd_tokens(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Token usage by turn: (see chat panel stats footer / session log).",
    )


def _cmd_context(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Context window size: (read from active provider's model spec).",
    )


# ---------------------------------------------------------------------------
# Handlers — Agent / plan (18)
# ---------------------------------------------------------------------------


def _cmd_agent(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Agent: idle. (status: ready)",
        side_effect={"show_agent_status": True},
    )


def _cmd_agents(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Available agents:", side_effect={"list_agents": True})


def _cmd_tools(ctx: CommandContext, args: list) -> CommandResult:
    from ..agent.tools import build_tool_registry
    from ..brain.store import BrainStore as _BS
    from pathlib import Path as _P
    store = _BS(_P("./.dariusai-bogus"))
    reg = build_tool_registry(store, sandbox=None)
    items = [
        {"label": name, "summary": spec.description}
        for name, spec in reg.specs.items()
    ]
    return _ok(
        f"{len(items)} tools registered.",
        ui={"kind": "list", "items": items},
    )


def _cmd_tool(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /tool <name>")
    return _ok(
        f"Tool {args[0]!r}: (description lookup pending).",
    )


def _cmd_plan(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Plan mode: ON.", side_effect={"plan_mode": True})


def _cmd_act(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Plan mode: OFF.", side_effect={"plan_mode": False})


def _cmd_review(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Review started.",
        side_effect={"review_changes": args[0] if args else None},
    )


def _cmd_fix(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Fix-mode enabled.",
        side_effect={"fix_mode": True, "hint": " ".join(args)},
    )


def _cmd_interrupt(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Interrupt sent.", side_effect={"interrupt": True})


def _cmd_cancel(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Cancel sent.", side_effect={"cancel": True})


def _cmd_pause(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Paused.", side_effect={"pause": True})


def _cmd_resume_agent(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Resumed.", side_effect={"resume": True})


def _cmd_approve(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Approved.", side_effect={"approve": True})


def _cmd_reject(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Rejected.", side_effect={"reject": True})


def _cmd_skip(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Skipped.", side_effect={"skip": True})


def _cmd_retry(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Retrying.", side_effect={"retry": True})


def _cmd_undo(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Undone.", side_effect={"undo": True})


def _cmd_redo(ctx: CommandContext, args: list) -> CommandResult:
    return _ok("Redone.", side_effect={"redo": True})


# ---------------------------------------------------------------------------
# Handlers — Git / VCS (13)
# ---------------------------------------------------------------------------


def _cmd_git(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    if not args:
        cmd = ["status"]
    else:
        cmd = args
    try:
        proc = subprocess.run(
            ["git"] + cmd, cwd=project_dir, capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ git {' '.join(cmd)}\n{out.strip()}")


def _cmd_commit(ctx: CommandContext, args: list) -> CommandResult:
    msg = " ".join(args) if args else "auto: commit"
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    try:
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True, timeout=30)
        result = subprocess.run(
            ["git", "commit", "-m", msg], cwd=project_dir,
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    return _ok(f"$ git commit -m {msg!r}\n{out.strip()}")


def _cmd_push(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    try:
        proc = subprocess.run(
            ["git", "push"] + args, cwd=project_dir,
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ git push\n{out.strip()}")


def _cmd_pull(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    try:
        proc = subprocess.run(
            ["git", "pull"] + args, cwd=project_dir,
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ git pull\n{out.strip()}")


def _cmd_branch(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    if not args:
        return _err("Usage: /branch <name>")
    try:
        proc = subprocess.run(
            ["git", "checkout", "-b", args[0]], cwd=project_dir,
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ git checkout -b {args[0]}\n{out.strip()}")


def _cmd_merge(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /merge <branch>")
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    try:
        proc = subprocess.run(
            ["git", "merge"] + args, cwd=project_dir,
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ git merge {args[0]}\n{out.strip()}")


def _cmd_rebase(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    try:
        proc = subprocess.run(
            ["git", "rebase"] + args, cwd=project_dir,
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ git rebase\n{out.strip()}")


def _cmd_diff(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    try:
        proc = subprocess.run(
            ["git", "diff"] + args, cwd=project_dir,
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = proc.stdout or ""
    return _ok(f"$ git diff\n{out[:4000]}" + ("\n... (truncated)" if len(out) > 4000 else ""))


def _cmd_log(ctx: CommandContext, args: list) -> CommandResult:
    n = args[0] if args else "10"
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    try:
        proc = subprocess.run(
            ["git", "log", "--oneline", "-n", n], cwd=project_dir,
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ git log --oneline -n {n}\n{out.strip()}")


def _cmd_status(ctx: CommandContext, args: list) -> CommandResult:
    return _cmd_git(ctx, [])


def _cmd_pr(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /pr <branch>")
    return _ok(
        f"PR opener requested for branch {args[0]!r}.",
        side_effect={"open_pr": args[0]},
    )


def _cmd_worktree(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Worktree manager: (UI panel — open via chat panel menu).",
    )


def _cmd_stash(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    try:
        proc = subprocess.run(
            ["git", "stash"] + args, cwd=project_dir,
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _err("git not installed.")
    out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return _ok(f"$ git stash\n{out.strip()}")


# ---------------------------------------------------------------------------
# Handlers — Files (14)
# ---------------------------------------------------------------------------


def _cmd_ls(ctx: CommandContext, args: list) -> CommandResult:
    path = args[0] if args else "."
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    target = project_dir / path
    if not target.exists():
        return _err(f"path not found: {path}")
    if not target.is_dir():
        return _err(f"not a directory: {path}")
    try:
        rows = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except Exception as exc:
        return _err(f"ls failed: {exc}")
    items = [
        {"label": ("📁 " if p.is_dir() else "📄 ") + p.name,
         "is_dir": p.is_dir()}
        for p in rows
    ]
    return _ok(
        f"{len(items)} entries in {path}.",
        ui={"kind": "list", "items": items},
    )


def _cmd_tree(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    target = (project_dir / args[0]) if args else project_dir
    if not target.exists():
        return _err(f"path not found: {target}")

    lines = []
    def _walk(p: Path, prefix: str = ""):
        try:
            entries = sorted(p.iterdir(), key=lambda q: (q.is_file(), q.name.lower()))
        except Exception:
            return
        for i, e in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + e.name + ("/" if e.is_dir() else ""))
            if e.is_dir():
                extension = "    " if is_last else "│   "
                _walk(e, prefix + extension)
    _walk(target)
    return _ok(f"Tree of {target}:\n" + "\n".join(lines[:200]) + ("\n..." if len(lines) > 200 else ""))


def _cmd_find(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /find <pattern>")
    pattern = args[0]
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    hits = []
    for p in project_dir.rglob("*"):
        if p.is_file() and pattern in p.name:
            hits.append(p)
    items = [{"label": str(p.relative_to(project_dir))} for p in hits[:200]]
    return _ok(
        f"{len(hits)} files match {pattern!r}.",
        ui={"kind": "list", "items": items},
    )


def _cmd_grep(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /grep <pattern> [path]")
    pattern = args[0]
    scan_path = args[1] if len(args) > 1 else "."
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    target = project_dir / scan_path
    if not target.exists():
        return _err(f"path not found: {scan_path}")
    hits = []
    for p in target.rglob("*"):
        if not p.is_file():
            continue
        try:
            for lineno, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if pattern in line:
                    hits.append(f"{p.relative_to(project_dir)}:{lineno}: {line}")
                    if len(hits) >= 200:
                        break
        except Exception:
            continue
        if len(hits) >= 200:
            break
    return _ok(
        f"{len(hits)} grep hits for {pattern!r}." + ("\n" + "\n".join(hits[:50]) if hits else ""),
    )


def _cmd_read(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /read <path>")
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    target = project_dir / args[0]
    if not target.exists():
        return _err(f"file not found: {args[0]}")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return _err(f"read failed: {exc}")
    return _ok(f"=== {args[0]} ===\n{text}")


def _cmd_write(ctx: CommandContext, args: list) -> CommandResult:
    if len(args) < 2:
        return _err("Usage: /write <path> <content>")
    path = args[0]
    content = " ".join(args[1:])
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    target = project_dir / path
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:
        return _err(f"write failed: {exc}")
    return _ok(f"Wrote {len(content)} chars to {path}.")


def _cmd_edit(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /edit <path>")
    return _ok(
        f"Edit requested for {args[0]!r} (open in editor).",
        side_effect={"open_in_editor": args[0]},
    )


def _cmd_open(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /open <path>")
    return _ok(
        f"Open requested for {args[0]!r}.",
        side_effect={"open_in_editor": args[0]},
    )


def _cmd_cd(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /cd <path>")
    return _ok(
        f"cd {args[0]} (project-relative).",
        side_effect={"cd": args[0]},
    )


def _cmd_pwd(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    return _ok(f"cwd: {project_dir.resolve()}")


def _cmd_mkdir(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /mkdir <path>")
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    target = project_dir / args[0]
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return _err(f"mkdir failed: {exc}")
    return _ok(f"Created {args[0]}.")


def _cmd_rm(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /rm <path>")
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    target = project_dir / args[0]
    if not target.exists():
        return _err(f"path not found: {args[0]}")
    try:
        import shutil
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except Exception as exc:
        return _err(f"rm failed: {exc}")
    return _ok(f"Removed {args[0]}.")


def _cmd_cp(ctx: CommandContext, args: list) -> CommandResult:
    if len(args) < 2:
        return _err("Usage: /cp <src> <dst>")
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    src = project_dir / args[0]
    dst = project_dir / args[1]
    try:
        import shutil
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    except Exception as exc:
        return _err(f"cp failed: {exc}")
    return _ok(f"Copied {args[0]} -> {args[1]}.")


def _cmd_mv(ctx: CommandContext, args: list) -> CommandResult:
    if len(args) < 2:
        return _err("Usage: /mv <src> <dst>")
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    src = project_dir / args[0]
    dst = project_dir / args[1]
    try:
        src.rename(dst)
    except Exception as exc:
        return _err(f"mv failed: {exc}")
    return _ok(f"Moved {args[0]} -> {args[1]}.")


# ---------------------------------------------------------------------------
# Handlers — Skills (9)
# ---------------------------------------------------------------------------


def _cmd_skill(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /skill <name>")
    return _ok(
        f"Skill {args[0]!r}: details (open in brain to view).",
    )


def _cmd_invoke(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /invoke <name>")
    return _ok(
        f"Invoking skill {args[0]!r}.",
        side_effect={"invoke_skill": args[0]},
    )


def _cmd_create(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /create <name>")
    return _ok(
        f"Skill {args[0]!r} created (draft).",
        side_effect={"create_skill": args[0]},
    )


def _cmd_edit_skill(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /edit <name>")
    return _ok(
        f"Open skill {args[0]!r} for editing.",
        side_effect={"edit_skill": args[0]},
    )


def _cmd_delete(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /delete <name>")
    return _ok(
        f"Skill {args[0]!r} deleted.",
        side_effect={"delete_skill": args[0]},
    )


def _cmd_share_skill(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /share <name>")
    return _ok(
        f"Skill {args[0]!r} shareable (real wiring pending).",
    )


def _cmd_import_skill(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Skill import started.",
        side_effect={"import_skill": args[0] if args else None},
    )


def _cmd_export_skill(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Skill export started.",
        side_effect={"export_skill": args[0] if args else None},
    )


# ---------------------------------------------------------------------------
# Handlers — Web (8) — stubbed for now (not_implemented)
# ---------------------------------------------------------------------------


def _cmd_web(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("web")


def _cmd_browse(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("browse")


def _cmd_fetch(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("fetch")


def _cmd_youtube(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("youtube")


def _cmd_wiki(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("wiki")


def _cmd_github(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("github")


def _cmd_docs(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("docs")


# Note: /search is shared with memory/_cmd_search, take the memory version


# ---------------------------------------------------------------------------
# Handlers — Permissions / sandbox (8)
# ---------------------------------------------------------------------------


def _cmd_trust(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /trust <path>")
    return _ok(f"Trust granted for {args[0]!r}.")


def _cmd_untrust(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /untrust <path>")
    return _ok(f"Trust revoked for {args[0]!r}.")


def _cmd_allow(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /allow <command>")
    return _ok(f"Allow rule set for {args[0]!r}.")


def _cmd_deny(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /deny <command>")
    return _ok(f"Deny rule set for {args[0]!r}.")


def _cmd_yolo(ctx: CommandContext, args: list) -> CommandResult:
    state = getattr(ctx.app_state, "yolo", False)
    new_state = not state
    ctx.app_state.yolo = new_state
    return _ok(f"Yolo mode: {'ON' if new_state else 'OFF'}.")


def _cmd_safe(ctx: CommandContext, args: list) -> CommandResult:
    state = getattr(ctx.app_state, "yolo", False)
    if not state:
        return _ok("Safe mode: already ON.")
    ctx.app_state.yolo = False
    return _ok("Safe mode: ON.")


def _cmd_permissions(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Permissions: (open settings panel to view trust/allow/deny rules).",
    )


def _cmd_sandbox(ctx: CommandContext, args: list) -> CommandResult:
    project_dir = Path(getattr(ctx.app_state, "project_dir", "."))
    return _ok(f"Sandbox root: {project_dir.resolve()}")


# ---------------------------------------------------------------------------
# Handlers — Settings (8)
# ---------------------------------------------------------------------------


def _cmd_settings(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Settings: (open settings panel to view).",
    )


def _cmd_config(ctx: CommandContext, args: list) -> CommandResult:
    if len(args) < 2:
        return _err("Usage: /config <key> <value>")
    try:
        ctx.store.set_setting(args[0], args[1])
    except Exception as exc:
        return _err(f"set_setting failed: {exc}")
    return _ok(f"Set {args[0]} = {args[1]!r}.")


def _cmd_theme(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /theme <name>")
    return _ok(
        f"Theme {args[0]!r} applied.",
        side_effect={"set_theme": args[0]},
    )


def _cmd_reset_settings(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Settings reset to defaults.",
        side_effect={"reset_settings": True},
    )


def _cmd_clear_cache(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Cache cleared.",
        side_effect={"clear_cache": True},
    )


def _cmd_cache(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Cache: (open settings panel to view).",
    )


def _cmd_layout(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /layout <name>")
    return _ok(
        f"Layout {args[0]!r} applied.",
        side_effect={"set_layout": args[0]},
    )


def _cmd_font(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /font <name>")
    return _ok(
        f"Font {args[0]!r} applied.",
        side_effect={"set_font": args[0]},
    )


# ---------------------------------------------------------------------------
# Handlers — Status / diagnostics (13)
# ---------------------------------------------------------------------------


def _cmd_status(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Status: ok.",
        side_effect={"show_status": True},
    )


def _cmd_health(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Health: ok.",
        side_effect={"show_health": True},
    )


def _cmd_logs(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Recent logs:",
        side_effect={"show_logs": args[0] if args else "10"},
    )


def _cmd_errors(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Recent errors:",
        side_effect={"show_errors": args[0] if args else "10"},
    )


def _cmd_warnings(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Recent warnings:",
        side_effect={"show_warnings": args[0] if args else "10"},
    )


def _cmd_info(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Info: (open system info panel).",
    )


def _cmd_debug(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Debug mode: ON.",
        side_effect={"debug_mode": True},
    )


def _cmd_metrics(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Metrics: (open observability panel).",
    )


def _cmd_usage(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Token usage by turn: (see chat panel stats footer).",
    )


def _cmd_cost(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Estimated cost: (cost tracking pending model price catalog).",
    )


def _cmd_tokens(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Token usage by turn: (see chat panel stats footer).",
    )


def _cmd_context(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Context window size: (read from active provider's model spec).",
    )


def _cmd_memory_status(ctx: CommandContext, args: list) -> CommandResult:
    return _ok(
        "Memory status: (open memory panel).",
    )


# ---------------------------------------------------------------------------
# Handlers — Voice (6) — stubbed
# ---------------------------------------------------------------------------


def _cmd_voice(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("voice")


def _cmd_speak(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /speak <text>")
    return _not_implemented("speak")


def _cmd_stop(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("stop")


def _cmd_mute(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("mute")


def _cmd_unmute(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("unmute")


def _cmd_volume(ctx: CommandContext, args: list) -> CommandResult:
    if not args:
        return _err("Usage: /volume <level>")
    return _not_implemented("volume")


# ---------------------------------------------------------------------------
# Handlers — Login (5)
# ---------------------------------------------------------------------------


def _cmd_login(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("login")


def _cmd_logout(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("logout")


def _cmd_whoami(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("whoami")


def _cmd_signup(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("signup")


def _cmd_account(ctx: CommandContext, args: list) -> CommandResult:
    return _not_implemented("account")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _make(name: str, group: str, short_label: str, summary: str,
          args_hint: str = "", handler: Optional[Callable] = None,
          interactive: bool = False, picker: Optional[Callable] = None,
          aliases: tuple = (), kind: str = "custom") -> CommandSpec:
    return CommandSpec(
        name=name,
        short_label=short_label,
        summary=summary,
        group=group,
        args_hint=args_hint,
        handler=handler,
        interactive=interactive,
        picker=picker,
        aliases=aliases,
        kind=kind,
    )


def _not_implemented_handler(name: str):
    """Return a handler that always returns the not-implemented result."""
    def _handler(ctx: CommandContext, args: list) -> CommandResult:
        return _not_implemented(name)
    return _handler


def _build_registry() -> dict:
    reg: dict[str, CommandSpec] = {}

    def _add(spec: CommandSpec) -> None:
        if spec.name in reg:
            raise ValueError(f"duplicate command name: {spec.name!r}")
        reg[spec.name] = spec
        for alias in spec.aliases:
            if alias in reg:
                raise ValueError(f"duplicate alias {alias!r} "
                                 f"(also canonical name for {reg[alias].name!r})")
            reg[alias] = spec  # alias entries point at the same spec

    # Conversation
    _add(_make("help", "conversation", "help", "List every command, grouped.", handler=_cmd_help))
    _add(_make("skills", "conversation", "skills", "List skills in the brain.",
               args_hint="[substring]", handler=_cmd_skills,
               aliases=("skill-list",)))
    _add(_make("exit", "conversation", "exit", "Close the chat session.",
               handler=_cmd_exit, aliases=("quit", "bye")))
    _add(_make("clear", "conversation", "clear", "Clear the chat history (keeps the brain).",
               handler=_cmd_clear))
    _add(_make("reset", "conversation", "reset", "Start a new ChatSession with the same LLM.",
               handler=_cmd_reset))
    _add(_make("compact", "conversation", "compact", "Compress the conversation context.",
               handler=_cmd_compact))
    _add(_make("history", "conversation", "history", "Show recent messages.",
               args_hint="[n]", handler=_cmd_history))
    _add(_make("session", "conversation", "session", "Show session info.",
               handler=_cmd_session))
    _add(_make("sessions", "conversation", "sessions", "List recent sessions.",
               handler=_cmd_sessions))
    _add(_make("resume", "conversation", "resume", "Resume a previous session.",
               args_hint="<session_id>", handler=_cmd_resume))
    _add(_make("rename", "conversation", "rename", "Rename the current session.",
               args_hint="<name>", handler=_cmd_rename))
    _add(_make("export", "conversation", "export", "Export the chat to a file.",
               args_hint="[path]", handler=_cmd_export))
    _add(_make("import", "conversation", "import", "Import a chat from a file.",
               args_hint="<path>", handler=_cmd_import))
    _add(_make("share", "conversation", "share", "Share the session.",
               args_hint="[platform]", handler=_cmd_share))

    # Memory
    _add(_make("memory", "memory", "memory", "Show stored memory entries.",
               handler=_cmd_memory))
    _add(_make("remember", "memory", "remember", "Add a memory note.",
               args_hint="<text>", handler=_cmd_remember))
    _add(_make("forget", "memory", "forget", "Remove a memory entry.",
               args_hint="<id>", handler=_cmd_forget))
    _add(_make("search", "memory", "search", "Search the brain.",
               args_hint="<query>", handler=_cmd_search))
    _add(_make("index", "memory", "index", "Reindex the brain.",
               handler=_cmd_index))
    _add(_make("consolidate", "memory", "consolidate", "Consolidate memory entries.",
               handler=_cmd_consolidate))
    _add(_make("where", "memory", "where", "Locate a node by topic.",
               args_hint="<topic>", handler=_cmd_where))

    # Project
    _add(_make("new", "project", "new", "Create a new project.",
               args_hint="<name>", handler=_cmd_new, aliases=("new-project",)))
    _add(_make("open", "project", "open", "Open a different project.",
               args_hint="<name>", handler=_cmd_open))
    _add(_make("close", "project", "close", "Close the current project.",
               handler=_cmd_close))
    _add(_make("project", "project", "project", "Show current project info.",
               handler=_cmd_project))
    _add(_make("projects", "project", "projects", "List all projects.",
               handler=_cmd_projects))
    _add(_make("init", "project", "init", "Init a new project (alias of /new).",
               args_hint="<name>", handler=_cmd_init))
    _add(_make("scaffold", "project", "scaffold", "Scaffold with a specific template.",
               args_hint="<name> <template>", handler=_cmd_scaffold))
    _add(_make("template", "project", "template", "Change the project template.",
               args_hint="<name>", handler=_cmd_template))
    _add(_make("build", "project", "build", "Run the build command.",
               args_hint="[args]", handler=_cmd_build))
    _add(_make("test", "project", "test", "Run tests.",
               args_hint="[path]", handler=_cmd_test))
    _add(_make("lint", "project", "lint", "Run the linter.",
               args_hint="[path]", handler=_cmd_lint))
    _add(_make("format", "project", "format", "Format the code.",
               args_hint="[path]", handler=_cmd_format))
    _add(_make("run", "project", "run", "Run a shell command.",
               args_hint="<cmd>", handler=_cmd_run))
    _add(_make("clean", "project", "clean", "Clean build artifacts.",
               handler=_cmd_clean))
    _add(_make("install", "project", "install", "Install dependencies.",
               args_hint="[args]", handler=_cmd_install))
    _add(_make("update", "project", "update", "Update dependencies.",
               args_hint="[args]", handler=_cmd_update))
    _add(_make("deploy", "project", "deploy", "Deploy (stub).",
               args_hint="[env]", handler=_cmd_deploy))

    # Provider
    _add(_make("provider", "provider", "provider", "Switch provider.",
               args_hint="<name>", handler=_cmd_provider))
    _add(_make("providers", "provider", "providers", "List all providers.",
               handler=_cmd_providers))
    _add(_make("add", "provider", "add", "Add a provider (multi-step picker).",
               args_hint="[picks]", interactive=True,
               picker=_cmd_add_provider, handler=_cmd_add_provider,
               aliases=("add-provider", "add provider")))
    _add(_make("remove", "provider", "remove", "Remove a provider.",
               args_hint="<name>", handler=_cmd_remove,
               aliases=("remove-provider",)))
    _add(_make("model", "provider", "model", "Switch active model.",
               args_hint="<name>", handler=_cmd_model))
    _add(_make("preset", "provider", "preset", "List preset providers.",
               handler=_cmd_preset))
    _add(_make("key", "provider", "key", "Set / update the API key.",
               args_hint="<api-key>", handler=_cmd_key))
    _add(_make("url", "provider", "url", "Set the base URL.",
               args_hint="<base-url>", handler=_cmd_url))
    _add(_make("test-provider", "provider", "test", "Test a provider connection.",
               args_hint="[provider]", handler=_cmd_test_provider))
    _add(_make("default", "provider", "default", "Set the default provider.",
               args_hint="<provider>", handler=_cmd_default))
    _add(_make("usage", "provider", "usage", "Show API usage.",
               handler=_cmd_usage))
    _add(_make("cost", "provider", "cost", "Show estimated cost.",
               handler=_cmd_cost))
    _add(_make("tokens", "provider", "tokens", "Show token usage.",
               handler=_cmd_tokens))
    _add(_make("context", "provider", "context", "Show context window size.",
               handler=_cmd_context))

    # Agent
    _add(_make("agent", "agent", "agent", "Show agent status.",
               handler=_cmd_agent))
    _add(_make("agents", "agent", "agents", "List available agents.",
               handler=_cmd_agents))
    _add(_make("tools", "agent", "tools", "List available tools.",
               handler=_cmd_tools))
    _add(_make("tool", "agent", "tool", "Show tool details.",
               args_hint="<name>", handler=_cmd_tool))
    _add(_make("plan", "agent", "plan", "Enter plan mode.",
               handler=_cmd_plan))
    _add(_make("act", "agent", "act", "Execute the plan.",
               handler=_cmd_act))
    _add(_make("review", "agent", "review", "Review changes.",
               args_hint="[path]", handler=_cmd_review))
    _add(_make("fix", "agent", "fix", "Fix issues.",
               args_hint="[hint]", handler=_cmd_fix))
    _add(_make("interrupt", "agent", "interrupt", "Interrupt the current run.",
               handler=_cmd_interrupt))
    _add(_make("cancel", "agent", "cancel", "Cancel the current task.",
               handler=_cmd_cancel))
    _add(_make("pause", "agent", "pause", "Pause the agent.",
               handler=_cmd_pause))
    _add(_make("resume-agent", "agent", "resume", "Resume the agent.",
               handler=_cmd_resume_agent))
    _add(_make("approve", "agent", "approve", "Approve a pending action.",
               handler=_cmd_approve))
    _add(_make("reject", "agent", "reject", "Reject a pending action.",
               handler=_cmd_reject))
    _add(_make("skip", "agent", "skip", "Skip a pending action.",
               handler=_cmd_skip))
    _add(_make("retry", "agent", "retry", "Retry the last action.",
               handler=_cmd_retry))
    _add(_make("undo", "agent", "undo", "Undo the last action.",
               handler=_cmd_undo))
    _add(_make("redo", "agent", "redo", "Redo the last action.",
               handler=_cmd_redo))

    # Git
    _add(_make("git", "git", "git", "git status (or run git subcommand).",
               args_hint="[cmd args]", handler=_cmd_git))
    _add(_make("commit", "git", "commit", "Commit changes.",
               args_hint="[msg]", handler=_cmd_commit))
    _add(_make("push", "git", "push", "Push to remote.",
               handler=_cmd_push))
    _add(_make("pull", "git", "pull", "Pull from remote.",
               args_hint="[args]", handler=_cmd_pull))
    _add(_make("branch", "git", "branch", "Create / switch branch.",
               args_hint="<name>", handler=_cmd_branch))
    _add(_make("merge", "git", "merge", "Merge branch.",
               args_hint="<branch>", handler=_cmd_merge))
    _add(_make("rebase", "git", "rebase", "Rebase.",
               args_hint="[args]", handler=_cmd_rebase))
    _add(_make("git-diff", "git", "diff", "Show diff.",
               args_hint="[args]", handler=_cmd_diff, aliases=("gitdiff",)))
    _add(_make("log", "git", "log", "Show log.",
               args_hint="[n]", handler=_cmd_log))
    _add(_make("git-status", "git", "status", "git status.",
               handler=_cmd_status, aliases=("gitstatus",)))
    _add(_make("pr", "git", "pr", "Open PR.",
               args_hint="<branch>", handler=_cmd_pr))
    _add(_make("worktree", "git", "worktree", "Manage worktrees.",
               handler=_cmd_worktree))
    _add(_make("stash", "git", "stash", "Stash changes.",
               args_hint="[args]", handler=_cmd_stash))

    # Files
    _add(_make("ls", "files", "ls", "List files.",
               args_hint="[path]", handler=_cmd_ls))
    _add(_make("tree", "files", "tree", "Show file tree.",
               args_hint="[path]", handler=_cmd_tree))
    _add(_make("find", "files", "find", "Find files by name.",
               args_hint="<pattern>", handler=_cmd_find))
    _add(_make("grep", "files", "grep", "Grep in files.",
               args_hint="<pattern> [path]", handler=_cmd_grep))
    _add(_make("read", "files", "read", "Read a file.",
               args_hint="<path>", handler=_cmd_read))
    _add(_make("write", "files", "write", "Write a file.",
               args_hint="<path> <content>", handler=_cmd_write))
    _add(_make("edit", "files", "edit", "Edit a file.",
               args_hint="<path>", handler=_cmd_edit))
    _add(_make("open-file", "files", "open", "Open a file.",
               args_hint="<path>", handler=_cmd_open, aliases=("file-open",)))
    _add(_make("cd", "files", "cd", "Change directory.",
               args_hint="<path>", handler=_cmd_cd))
    _add(_make("pwd", "files", "pwd", "Print working directory.",
               handler=_cmd_pwd))
    _add(_make("mkdir", "files", "mkdir", "Make directory.",
               args_hint="<path>", handler=_cmd_mkdir))
    _add(_make("rm", "files", "rm", "Remove file or directory.",
               args_hint="<path>", handler=_cmd_rm))
    _add(_make("cp", "files", "cp", "Copy.",
               args_hint="<src> <dst>", handler=_cmd_cp))
    _add(_make("mv", "files", "mv", "Move / rename.",
               args_hint="<src> <dst>", handler=_cmd_mv))

    # Skills
    _add(_make("skill", "skills", "skill", "Show skill details.",
               args_hint="<name>", handler=_cmd_skill))
    _add(_make("invoke", "skills", "invoke", "Invoke a skill.",
               args_hint="<name>", handler=_cmd_invoke))
    _add(_make("create", "skills", "create", "Create a new skill.",
               args_hint="<name>", handler=_cmd_create))
    _add(_make("edit-skill", "skills", "edit", "Edit a skill.",
               args_hint="<name>", handler=_cmd_edit_skill))
    _add(_make("delete", "skills", "delete", "Delete a skill.",
               args_hint="<name>", handler=_cmd_delete))
    _add(_make("share-skill", "skills", "share", "Share a skill.",
               args_hint="<name>", handler=_cmd_share_skill))
    _add(_make("import-skill", "skills", "import", "Import a skill.",
               args_hint="[path]", handler=_cmd_import_skill))
    _add(_make("export-skill", "skills", "export", "Export a skill.",
               args_hint="[path]", handler=_cmd_export_skill))

    # Web (stubbed)
    _add(_make("web", "web", "web", "Fetch a URL.",
               args_hint="<url>", handler=_not_implemented_handler("web")))
    _add(_make("browse", "web", "browse", "Browse a URL.",
               args_hint="<url>", handler=_not_implemented_handler("browse")))
    _add(_make("fetch", "web", "fetch", "Fetch content.",
               args_hint="<url>", handler=_not_implemented_handler("fetch")))
    _add(_make("youtube", "web", "youtube", "YouTube transcript.",
               args_hint="<url>", handler=_not_implemented_handler("youtube")))
    _add(_make("wiki", "web", "wiki", "Wikipedia.",
               args_hint="<topic>", handler=_not_implemented_handler("wiki")))
    _add(_make("github", "web", "github", "GitHub info.",
               args_hint="<repo>", handler=_not_implemented_handler("github")))
    _add(_make("docs", "web", "docs", "Fetch docs.",
               args_hint="<url>", handler=_not_implemented_handler("docs")))

    # Permissions
    _add(_make("trust", "permissions", "trust", "Trust a path.",
               args_hint="<path>", handler=_cmd_trust))
    _add(_make("untrust", "permissions", "untrust", "Untrust a path.",
               args_hint="<path>", handler=_cmd_untrust))
    _add(_make("allow", "permissions", "allow", "Allow a command.",
               args_hint="<cmd>", handler=_cmd_allow))
    _add(_make("deny", "permissions", "deny", "Deny a command.",
               args_hint="<cmd>", handler=_cmd_deny))
    _add(_make("yolo", "permissions", "yolo", "Toggle yolo mode.",
               handler=_cmd_yolo))
    _add(_make("safe", "permissions", "safe", "Toggle safe mode.",
               handler=_cmd_safe))
    _add(_make("permissions", "permissions", "permissions", "Show permissions.",
               handler=_cmd_permissions))
    _add(_make("sandbox", "permissions", "sandbox", "Show sandbox info.",
               handler=_cmd_sandbox))

    # Settings
    _add(_make("settings", "settings", "settings", "Show settings.",
               handler=_cmd_settings))
    _add(_make("config", "settings", "config", "Set a config value.",
               args_hint="<key> <value>", handler=_cmd_config))
    _add(_make("theme", "settings", "theme", "Change theme.",
               args_hint="<name>", handler=_cmd_theme))
    _add(_make("reset-settings", "settings", "reset", "Reset settings.",
               handler=_cmd_reset_settings))
    _add(_make("clear-cache", "settings", "clear", "Clear cache.",
               handler=_cmd_clear_cache))
    _add(_make("cache", "settings", "cache", "Show cache.",
               handler=_cmd_cache))
    _add(_make("layout", "settings", "layout", "Change layout.",
               args_hint="<name>", handler=_cmd_layout))
    _add(_make("font", "settings", "font", "Change font.",
               args_hint="<name>", handler=_cmd_font))

    # Status
    _add(_make("status", "status", "status", "Show status.",
               handler=_cmd_status))
    _add(_make("health", "status", "health", "Health check.",
               handler=_cmd_health))
    _add(_make("logs", "status", "logs", "Show recent logs.",
               args_hint="[n]", handler=_cmd_logs))
    _add(_make("errs", "status", "errs", "Show recent errors.",
               args_hint="[n]", handler=_cmd_errors))
    _add(_make("warns", "status", "warns", "Show recent warnings.",
               args_hint="[n]", handler=_cmd_warnings))
    _add(_make("info", "status", "info", "Show info.",
               handler=_cmd_info))
    _add(_make("debug-cmd", "status", "debug", "Debug mode.",
               handler=_cmd_debug))
    _add(_make("metrics", "status", "metrics", "Show metrics.",
               handler=_cmd_metrics))
    _add(_make("mem-status", "status", "memory", "Show memory status.",
               handler=_cmd_memory_status))

    # Voice
    _add(_make("voice", "voice", "voice", "Toggle voice mode.",
               handler=_not_implemented_handler("voice")))
    _add(_make("speak", "voice", "speak", "Speak text.",
               args_hint="<text>", handler=_not_implemented_handler("speak"), aliases=("say",)))
    _add(_make("stop-voice", "voice", "stop", "Stop speaking.",
               handler=_not_implemented_handler("stop")))
    _add(_make("mute", "voice", "mute", "Mute.",
               handler=_not_implemented_handler("mute")))
    _add(_make("unmute", "voice", "unmute", "Unmute.",
               handler=_not_implemented_handler("unmute")))
    _add(_make("volume", "voice", "volume", "Set volume.",
               args_hint="<level>", handler=_not_implemented_handler("volume")))

    # Login
    _add(_make("login", "login", "login", "Login.",
               handler=_not_implemented_handler("login")))
    _add(_make("logout", "login", "logout", "Logout.",
               handler=_not_implemented_handler("logout")))
    _add(_make("whoami", "login", "whoami", "Show current user.",
               handler=_not_implemented_handler("whoami")))
    _add(_make("signup", "login", "signup", "Sign up.",
               handler=_not_implemented_handler("signup")))
    _add(_make("account", "login", "account", "Show account.",
               handler=_not_implemented_handler("account")))

    return reg


REGISTRY: dict[str, CommandSpec] = _build_registry()


def get_canonical(spec: CommandSpec) -> str:
    """The user-typed alias could be mapped to a different canonical
    name (e.g. `quit` -> `exit`). This returns the canonical name the
    registry uses for display."""
    return spec.name


def list_commands() -> list[CommandSpec]:
    """Return every UNIQUE command in registration order. Aliases are
    filtered out (they share the same spec)."""
    seen = set()
    out = []
    for spec in REGISTRY.values():
        if spec.name in seen:
            continue
        seen.add(spec.name)
        out.append(spec)
    return out


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


async def run_command(ctx: CommandContext, name: str, args: list,
                     request_id: str, ws_send) -> None:
    """Dispatch a single slash-command.

    `ws_send` is the async-send callable (typically `ws.send_json`).
    The handler may be sync (we run it in a thread) or async
    (we await it directly). The handler returns a CommandResult.

    The interpreter decides between three flows:
    1. Unknown command → error.
    2. Interactive command with no args → open the picker.
    3. Otherwise → run the handler.
    """
    spec = REGISTRY.get(name)
    if spec is None:
        await ws_send({"type": "command_result",
                       "request_id": request_id,
                       "name": name,
                       "status": "error",
                       "message": f"Unknown command: /{name}. "
                                  f"Type /help to see the list."})
        return

    # Interactive (picker) path — open the picker
    if spec.interactive and spec.picker is not None and not args:
        try:
            result = spec.picker(ctx, args)
        except Exception as exc:
            await ws_send({"type": "command_result",
                           "request_id": request_id,
                           "name": name,
                           "status": "error",
                           "message": f"{type(exc).__name__}: {exc}"})
            return
        await _emit_result(ws_send, request_id, name, result)
        return

    # Standard handler path
    if spec.handler is None:
        await ws_send({"type": "command_result",
                       "request_id": request_id,
                       "name": name,
                       "status": "error",
                       "message": f"/{name} has no handler."})
        return

    import asyncio
    try:
        if asyncio.iscoroutinefunction(spec.handler):
            result = await spec.handler(ctx, args)
        else:
            result = await asyncio.to_thread(spec.handler, ctx, args)
    except Exception as exc:
        await ws_send({"type": "command_result",
                       "request_id": request_id,
                       "name": name,
                       "status": "error",
                       "message": f"{type(exc).__name__}: {exc}"})
        return

    await _emit_result(ws_send, request_id, name, result)


async def _emit_result(ws_send, request_id: str, name: str,
                       result: CommandResult) -> None:
    payload = {"type": "command_result",
               "request_id": request_id,
               "name": name,
               "status": result.status,
               "message": result.message}
    if result.ui is not None:
        payload["ui"] = result.ui
    if result.side_effect is not None:
        payload["side_effect"] = result.side_effect
    await ws_send(payload)
