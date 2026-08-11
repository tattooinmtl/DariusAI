"""Screenshot the real UI without opening a window on anyone's desktop.

    .venv\Scripts\python.exe tools/render_preview.py out.png

Seeds a throwaway brain with a realistic spread of nodes, serves the actual
page, and renders it in headless Edge. This exists because the UI was
previously written blind — CSS reasoned about rather than looked at — which
is exactly how a panel ends up with its text running off the edge of the
window and nobody noticing. Look at the PNG before calling a visual change
done."""
import sys, subprocess, tempfile
sys.path.insert(0, r"C:\.dariusai-harness\src")
from pathlib import Path
from dariusai.brain.store import BrainStore
from dariusai.brain.skill import Skill
from dariusai.viz.server import create_app
from dariusai.viz.window import _start_server

SHOT = Path(sys.argv[1] if len(sys.argv) > 1 else "app.png")
home = Path(tempfile.mkdtemp()) / "brain"
store = BrainStore(home)
SEED = [
    ("python-async", "Async/await in Python", "language", ["python", "asyncio"]),
    ("python-pkg", "Packaging with pyproject", "language", ["python", "build"]),
    ("rust-owner", "Rust ownership model", "language", ["rust", "memory"]),
    ("ts-generics", "TypeScript generics", "language", ["typescript"]),
    ("tool-ripgrep", "ripgrep for fast search", "tool", ["search", "cli"]),
    ("tool-pytest", "pytest fixtures", "tool", ["testing", "python"]),
    ("tool-docker", "Docker multi-stage builds", "tool", ["docker"]),
    ("pat-retry", "Exponential backoff retry", "pattern", ["resilience"]),
    ("pat-cqrs", "CQRS read/write split", "pattern", ["architecture"]),
    ("pat-eventbus", "In-process event bus", "pattern", ["architecture"]),
    ("proj-harness", "DariusAI harness", "project", ["dariusai"]),
    ("proj-viz", "Neural viz panel", "project", ["dariusai", "ui"]),
    ("skill-webscrape", "Scraping with ddgs", "skill", ["web"]),
    ("skill-sqlite", "SQLite WAL tuning", "skill", ["db"]),
    ("skill-dpapi", "DPAPI secret storage", "skill", ["windows", "security"]),
    ("pref-dark", "Dark theme everywhere", "preference", ["ui"]),
]
for sid, title, cat, tags in SEED:
    store.add_skill(Skill(id=sid, title=title, category=cat, tags=tags,
                          problem=f"How to handle {title.lower()}?",
                          solution=f"Notes on {title}.", sources=[]))

app = create_app(home, project_dir=Path(r"C:\.dariusai-harness"))
server, port = _start_server(app, "127.0.0.1", 19000)
edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
subprocess.run([edge, "--headless=new", "--disable-gpu", "--no-first-run",
                f"--user-data-dir={tempfile.mkdtemp()}", "--virtual-time-budget=9000",
                "--window-size=1280,840", f"--screenshot={SHOT}", f"http://127.0.0.1:{port}/"],
               capture_output=True, timeout=180)
print("nodes:", len(store.to_graph_payload()["nodes"]), "edges:", len(store.to_graph_payload()["edges"]))
print("shot:", SHOT, SHOT.exists() and SHOT.stat().st_size)
server.should_exit = True
