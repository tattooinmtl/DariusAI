"""Entry point:

  dariusai viz            — open the floating neural-network window (pywebview)
  dariusai serve           — run just the viz HTTP/WS server (browser access
                              instead of a native window)
  dariusai run "<task>"    — run the agent loop once on a task (Planner ->
                              Coder -> Tester -> Verifier -> Memory Writer)
  dariusai chat            — interactive terminal REPL (like omni's), a
                              long-lived ChatSession instead of one task
  dariusai install-shortcuts — Desktop/Start Menu shortcuts carrying the
                              brain icon instead of Python's
  dariusai --version       — print the version (alpha 0.55 today)

`run` and `chat` need ANTHROPIC_API_KEY — the only real model calls in this
project. Everything else (graph control flow, the REPL's I/O loop) is
unit-tested against a stub LLM.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

DEFAULT_HOME = Path.home() / ".dariusai"
DEFAULT_PROJECT_DIR = Path.home() / "DariusAI-Creations"


def _resolve_project_dir(raw: str | Path) -> Path:
    """Normalize and ensure the working folder exists.

    A dedicated creations directory keeps generated files out of random
    locations, while still allowing users to point the app anywhere else.
    """
    path = Path(raw).expanduser()
    if path.exists() and not path.is_dir():
        raise SystemExit(f"project dir is a file, not a folder: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def run_repl(session, input_fn: Callable[[str], str] = input, print_fn: Callable[[str], None] = print) -> None:
    """The REPL's I/O loop, factored out from cmd_chat so it's testable
    against a ChatSession backed by a stub LLM — no real terminal or API
    key needed to verify this logic."""
    print_fn("DariusAI — type a message, /exit to quit.")
    while True:
        try:
            raw = input_fn("\nyou> ")
        except (EOFError, KeyboardInterrupt):
            print_fn("")
            return
        text = raw.strip()
        if not text:
            continue
        if text in ("/exit", "/quit"):
            return

        def on_event(ev: dict[str, Any]) -> None:
            if ev["type"] == "assistant_text":
                print_fn(f"\ndarius> {ev['text']}")
            elif ev["type"] == "tool_call_start":
                print_fn(f"  [tool] {ev['name']}({ev['input']})")
            elif ev["type"] == "tool_call_result":
                preview = ev["result"][:200].replace("\n", " ")
                print_fn(f"  [result] {preview}")

        try:
            session.send(text, on_event=on_event)
        except Exception as exc:  # a bad key / network failure / tool bug ends the turn, not the whole session
            print_fn(f"\n[error] {exc}")


def cmd_viz(args: argparse.Namespace) -> None:
    from .viz.window import launch
    launch(Path(args.home), host=args.host, port=args.port, project_dir=_resolve_project_dir(args.project_dir))


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn
    from .viz.server import create_app
    app = create_app(Path(args.home), project_dir=_resolve_project_dir(args.project_dir))
    uvicorn.run(app, host=args.host, port=args.port)


def _require_llm_configured(store) -> None:
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    if store.get_active_provider() and store.get_active_provider()["has_api_key"]:
        return
    raise SystemExit(
        "No LLM configured — either export ANTHROPIC_API_KEY, or set an active provider "
        "with an API key in Settings (dariusai viz, then File → Settings)."
    )


def cmd_run(args: argparse.Namespace) -> None:
    from .agent.graph import run_agent
    from .agent.llm import build_llm
    from .agent.sandbox import Sandbox
    from .agent.tools import build_tool_registry
    from .brain.store import BrainStore

    store = BrainStore(Path(args.home))
    _require_llm_configured(store)
    project_dir = _resolve_project_dir(args.project_dir)
    # The agent acts on its own — it gets the project directory as its
    # sandbox, not the whole filesystem.
    tools = build_tool_registry(store, Sandbox(root=project_dir))
    llm = build_llm(store)
    result = run_agent(args.task, llm, tools, project_dir=project_dir, max_retries=args.max_retries)
    print(f"\nverdict: {result.get('verdict')}")
    if result.get("coder_summary"):
        print(f"summary: {result['coder_summary']}")


def cmd_chat(args: argparse.Namespace) -> None:
    from .agent.chat import ChatSession
    from .agent.llm import build_llm
    from .agent.sandbox import Sandbox
    from .agent.tools import build_tool_registry
    from .brain.store import BrainStore

    store = BrainStore(Path(args.home))
    _require_llm_configured(store)
    project_dir = _resolve_project_dir(args.project_dir)
    tools = build_tool_registry(store, Sandbox(root=project_dir))
    session = ChatSession(llm=build_llm(store), tools=tools)
    run_repl(session)


def cmd_import_skills(args: argparse.Namespace) -> None:
    from .brain.omni_import import import_skills
    from .brain.store import BrainStore

    store = BrainStore(Path(args.home))
    result = import_skills(store, args.source) if args.source else import_skills(store)
    print(f"imported {result['imported']} skill(s) from {result['source']}")
    for category, count in sorted(result["by_category"].items()):
        print(f"  {category}: {count}")
    if result["skipped"]:
        print(f"  skipped: {result['skipped']}")


def cmd_install_shortcuts(args: argparse.Namespace) -> None:
    from .os_integration import install_shortcuts
    for path in install_shortcuts(desktop=not args.no_desktop, start_menu=not args.no_start_menu):
        print(f"created {path}")


def cmd_import_addon(args: argparse.Namespace) -> None:
    from .brain.omni_import import import_addon
    from .brain.store import BrainStore
    result = import_addon(BrainStore(Path(args.home)), args.source)
    print(f"imported {result['imported']} skills and {result['hooks']} hooks from {result['source']}")
    for group in result["groups"]:
        print(f"  branch {group}")
    for category, count in sorted(result["by_category"].items()):
        print(f"    {category:24} {count}")


def build_parser() -> argparse.ArgumentParser:
    from . import VERSION_DISPLAY, __version__

    parser = argparse.ArgumentParser(prog="dariusai")
    # argparse fires --version while parsing, so it still works even though a
    # subcommand is otherwise required.
    parser.add_argument(
        "--version", action="version", version=f"dariusai {__version__} ({VERSION_DISPLAY})"
    )
    parser.add_argument("--home", default=str(DEFAULT_HOME), help="brain/skills storage directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p_viz = sub.add_parser("viz", help="open the floating neural-network window")
    p_viz.add_argument("--host", default="127.0.0.1")
    p_viz.add_argument("--port", type=int, default=8765)
    p_viz.add_argument(
        "--project-dir",
        default=str(DEFAULT_PROJECT_DIR),
        help="directory the code editor pane browses (default: ~/DariusAI-Creations)",
    )
    p_viz.set_defaults(func=cmd_viz)

    p_serve = sub.add_parser("serve", help="run the viz server without a native window")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument(
        "--project-dir",
        default=str(DEFAULT_PROJECT_DIR),
        help="directory the code editor pane browses (default: ~/DariusAI-Creations)",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_run = sub.add_parser("run", help="run the agent loop on a task (needs ANTHROPIC_API_KEY)")
    p_run.add_argument("task")
    p_run.add_argument("--project-dir", default=str(DEFAULT_PROJECT_DIR))
    p_run.add_argument("--max-retries", type=int, default=2)
    p_run.set_defaults(func=cmd_run)

    p_chat = sub.add_parser("chat", help="interactive terminal REPL (needs ANTHROPIC_API_KEY)")
    p_chat.add_argument(
        "--project-dir",
        default=str(DEFAULT_PROJECT_DIR),
        help="the sandbox the agent works inside (default: ~/DariusAI-Creations)",
    )
    p_chat.set_defaults(func=cmd_chat)

    p_import = sub.add_parser("import-skills", help="import omni's skill library into the brain")
    p_import.add_argument("--source", default=None, help="path to a skills/ directory (default: ~/.omni/skills)")
    p_import.set_defaults(func=cmd_import_skills)

    p_addon = sub.add_parser("import-addon",
                             help="import addon/skills and addon/hooks into the brain")
    p_addon.add_argument("--source", default=None, help="addon directory (default: ./addon)")
    p_addon.set_defaults(func=cmd_import_addon)

    p_shortcuts = sub.add_parser(
        "install-shortcuts", help="create Desktop/Start Menu shortcuts that carry the brain icon"
    )
    p_shortcuts.add_argument("--no-desktop", action="store_true")
    p_shortcuts.add_argument("--no-start-menu", action="store_true")
    p_shortcuts.set_defaults(func=cmd_install_shortcuts)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
