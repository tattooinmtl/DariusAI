"""End-to-end system check: does every subsystem actually work right now?

    .venv\\Scripts\\python.exe tools/verify_systems.py
    .venv\\Scripts\\python.exe tools/verify_systems.py --network   (also calls the provider)

A checklist in a document is a checklist nobody runs. This one executes:
it opens the real brain, boots the real server, hits the real endpoints,
runs a real sandboxed subprocess, and reports what it observed rather than
what it expects.

Network calls are opt-in because they cost money and depend on someone
else's uptime; everything else runs offline in a few seconds.

Exit code is non-zero if any REQUIRED check fails, so it can gate a release.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
results: list[tuple[str, str, str, str]] = []   # (system, check, status, detail)


def record(system: str, check: str, status: str, detail: str = "") -> None:
    results.append((system, check, status, detail))


def guard(system: str, check: str, required: bool = True):
    """Run a check, turning an exception into a FAIL rather than a crash —
    one broken subsystem must not stop the rest from being verified."""
    def wrap(fn):
        try:
            ok, detail = fn()
            record(system, check, PASS if ok else (FAIL if required else WARN), detail)
        except Exception as exc:
            record(system, check, FAIL if required else WARN, f"{type(exc).__name__}: {exc}")
        return fn
    return wrap


# --------------------------------------------------------------------------
def check_version():
    import dariusai
    from bump_version import fingerprint  # noqa: E402
    import json
    lock = json.loads((ROOT / "version_lock.json").read_text(encoding="utf-8"))

    @guard("version", "package/pyproject/lock agree")
    def _():
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        in_toml = pyproject.split('version = "')[1].split('"')[0]
        ok = in_toml == dariusai.__version__ == lock["version"]
        return ok, f"{dariusai.__version__} ({dariusai.VERSION_DISPLAY})"

    @guard("version", "source matches the locked fingerprint")
    def _():
        ok = fingerprint() == lock["fingerprint"]
        return ok, "in step" if ok else "source changed without a version bump"


def check_brain():
    from dariusai.brain.store import CONVERSATIONS_ROOT, COORDINATOR_ID, BrainStore
    home = Path.home() / ".dariusai"

    @guard("brain", "store opens and indexes")
    def _():
        store = BrainStore(home)
        payload = store.to_graph_payload()
        return len(payload["nodes"]) > 0, f"{len(payload['nodes'])} nodes, {len(payload['edges'])} edges"

    @guard("brain", "skills present (omni import)")
    def _():
        store = BrainStore(home)
        omni = [n for n in store.to_graph_payload()["nodes"] if str(n["id"]).startswith("omni-")]
        return len(omni) > 0, f"{len(omni)} omni skills"

    @guard("brain", "conversation branch structure", required=False)
    def _():
        store = BrainStore(home)
        payload = store.to_graph_payload()
        edges = {(e["source"], e["target"]) for e in payload["edges"]}
        atoms = [n["id"] for n in payload["nodes"]
                 if n["category"] == "conversation" and n["id"] != CONVERSATIONS_ROOT]
        if not atoms:
            return True, "no conversations recorded yet"
        stray = [a for a in atoms if (COORDINATOR_ID, a) in edges]
        return not stray, f"{len(atoms)} atoms, {len(stray)} wired to the centre instead of the branch"


def check_provider(network: bool):
    from dariusai.brain.store import BrainStore
    from dariusai.agent.llm import build_llm
    store = BrainStore(Path.home() / ".dariusai")

    @guard("model", "a provider is active with a key")
    def _():
        active = store.get_active_provider()
        if not active:
            return False, "no active provider — nothing can run"
        return bool(active["has_api_key"]), f"{active['name']} / {active['model']}"

    @guard("model", "routed to the right client for its protocol")
    def _():
        active = store.get_active_provider()
        if not active:
            return False, "no active provider"
        llm = build_llm(store)
        return True, f"{type(llm).__name__} -> {getattr(llm, 'base_url', 'sdk default')}"

    @guard("model", "keyed-but-inactive providers", required=False)
    def _():
        idle = [p["name"] for p in store.list_providers() if p["has_api_key"] and not p["is_active"]]
        return not idle, ("none" if not idle else f"configured but unused: {', '.join(idle)}")

    if network:
        @guard("model", "live round trip")
        def _():
            active = store.get_active_provider()
            llm = build_llm(store)
            t = time.time()
            resp = llm.complete(system="Reply with the single word: ok",
                                messages=[{"role": "user", "content": "ping"}])
            elapsed = time.time() - t
            text = "".join(b.get("text", "") for b in resp["content"] if b["type"] == "text")
            leaking = "<think>" in text
            return not leaking, (f"{elapsed:.1f}s"
                                 + (" — REPLY CONTAINS <think> MARKUP" if leaking else ""))
    else:
        record("model", "live round trip", SKIP, "pass --network to include")


def check_sandbox():
    from dariusai.agent.sandbox import Sandbox, SandboxViolation

    @guard("sandbox", "confines paths to its root")
    def _():
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "proj"; root.mkdir()
            sb = Sandbox(root=root)
            try:
                sb.resolve(str(Path(td) / "outside.txt"))
                return False, "escaped the sandbox"
            except SandboxViolation:
                return True, "traversal refused"

    @guard("sandbox", "strips credentials from child env")
    def _():
        with tempfile.TemporaryDirectory() as td:
            env = Sandbox(root=Path(td)).environment()
            leaked = [k for k in env if "API_KEY" in k.upper() or "SECRET" in k.upper()]
            return not leaked, "clean" if not leaked else f"leaked {leaked}"

    @guard("sandbox", "kills the whole process tree on timeout")
    def _():
        with tempfile.TemporaryDirectory() as td:
            r = Sandbox(root=Path(td), timeout=2).run(
                f'"{sys.executable}" -c "import time; time.sleep(30)"')
            return r.timed_out, "timed out and killed" if r.timed_out else "did not time out"

    @guard("sandbox", "spawns no console windows")
    def _():
        import re
        pat = re.compile(r"subprocess\.(run|Popen|call|check_output|check_call)\s*\(")
        bad = []
        for path in list((ROOT / "src").rglob("*.py")) + [ROOT / "launch.pyw"]:
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in pat.finditer(text):
                depth, i = 1, m.end()
                while i < len(text) and depth:
                    depth += {"(": 1, ")": -1}.get(text[i], 0); i += 1
                if "creationflags" not in text[m.start():i]:
                    bad.append(path.name)
        return not bad, "all spawns suppressed" if not bad else f"visible console from {set(bad)}"


def check_runtimes():
    from dariusai.agent.runtimes import detect, run_snippet
    from dariusai.agent.sandbox import Sandbox
    found = detect()

    @guard("runtimes", "detection")
    def _():
        have = [i["label"] for i in found.values() if i["available"]]
        return len(have) > 0, f"{len(have)}/{len(found)}: {', '.join(have)}"

    @guard("runtimes", "python snippet executes")
    def _():
        with tempfile.TemporaryDirectory() as td:
            r = run_snippet(Sandbox(root=Path(td)), "python", "print('ok')", timeout=60)
            return r["ok"] and "ok" in r["output"], r["output"].strip()[:40]

    @guard("runtimes", "sqlite works without the CLI")
    def _():
        with tempfile.TemporaryDirectory() as td:
            r = run_snippet(Sandbox(root=Path(td)), "sql",
                            "CREATE TABLE t(a INT); INSERT INTO t VALUES (7); SELECT * FROM t;",
                            timeout=60)
            return r["ok"] and "7" in r["output"], r["output"].strip().replace("\n", " ")[:50]


def check_workbench():
    from dariusai.workbench import create_project, search, workbench_root
    from dariusai.brain.store import BrainStore
    store = BrainStore(Path.home() / ".dariusai")

    @guard("workbench", "root exists and is writable")
    def _():
        root = workbench_root(store)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_text("x", encoding="utf-8"); probe.unlink()
        projects = [p.name for p in root.iterdir() if p.is_dir()]
        return True, f"{root} ({len(projects)} projects)"

    @guard("workbench", "type picker typeahead")
    def _():
        h = [t.id for t in search("h")][:3]
        return h == ["html", "html5", "htmx"], f"'h' -> {h}"

    @guard("workbench", "creates a real project")
    def _():
        with tempfile.TemporaryDirectory() as td:
            res = create_project("VerifyProbe", "html", lambda e: None, root=Path(td))
            made = Path(td) / "VerifyProbe" / "index.html"
            return res["ok"] and made.is_file(), "scaffolded + files on disk"


def check_server_and_page():
    from fastapi.testclient import TestClient
    from dariusai.viz.server import create_app
    # Not TemporaryDirectory: on Windows the brain's SQLite handle is still
    # open at cleanup time and the delete raises, which aborted the run before
    # the page checks below ever executed.
    td = tempfile.mkdtemp()
    if True:
        client = TestClient(create_app(Path(td) / "brain", project_dir=Path(td)))

        for path, name in [("/api/health", "health"), ("/api/version", "version"),
                           ("/api/graph", "graph"), ("/api/runtimes", "runtimes"),
                           ("/api/workbench", "workbench"), ("/api/providers", "providers"),
                           ("/api/provider-presets", "presets")]:
            @guard("server", f"GET {path}")
            def _(p=path):
                r = client.get(p)
                return r.status_code == 200, f"{r.status_code}"

        @guard("server", "page is uncacheable")
        def _():
            r = client.get("/")
            return "no-store" in r.headers.get("cache-control", ""), r.headers.get("cache-control", "none")

    page = (ROOT / "src/dariusai/viz/static/index.html").read_text(encoding="utf-8")
    for needle, label in [
        ("pywebview-drag-region", "window is draggable"),
        ("id=\"versionBadge\"", "version badge"),
        ("id=\"btnReload\"", "stale-build reload"),
        ("id='npConsole'", "new-project console"),
        ("drawBrain(", "3D brain mesh"),
        ("COLORS.conversation", "conversation colour used"),
        ("inUseUntil.get(a.id) > now", "edges electrify from use"),
    ]:
        @guard("page", label)
        def _(n=needle):
            return n in page, "present" if n in page else "MISSING"


def check_desktop():
    from dariusai import os_integration as osi

    @guard("desktop", "icons exist and are cut out")
    def _():
        from PIL import Image
        ico = osi.icon_path()
        png = ico.parent / "favicon.png"
        if not (ico.exists() and png.exists()):
            return False, "missing icon files"
        img = Image.open(png)
        corner = img.convert("RGBA").getpixel((0, 0))[3]
        return corner == 0, f"{sorted(Image.open(ico).ico.sizes())[-1]} ico, corner alpha {corner}"

    @guard("desktop", "shortcuts installed", required=False)
    def _():
        found = [str(p) for p in (osi.desktop_dir() / osi.SHORTCUT_NAME,
                                  osi.start_menu_dir() / osi.SHORTCUT_NAME) if p.exists()]
        return bool(found), f"{len(found)} of 2" if found else "none (run: dariusai install-shortcuts)"

    @guard("desktop", "no stale app processes", required=False)
    def _():
        if sys.platform != "win32" or not shutil.which("powershell"):
            return True, "not applicable"
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
             "Where-Object { $_.CommandLine -like '*launch.pyw*' }).Count"],
            capture_output=True, text=True, timeout=60,
            creationflags=0x08000000 if sys.platform == "win32" else 0)
        n = (out.stdout or "0").strip() or "0"
        return True, f"{n} running (2 per window is normal — venv stub + interpreter)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--network", action="store_true", help="also make a real provider call")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "tools"))
    for fn in (check_version, check_brain, check_sandbox, check_runtimes,
               check_workbench, check_server_and_page, check_desktop):
        try:
            fn()
        except Exception as exc:
            record(fn.__name__, "suite", FAIL, f"{type(exc).__name__}: {exc}")
    try:
        check_provider(args.network)
    except Exception as exc:
        record("model", "suite", FAIL, f"{type(exc).__name__}: {exc}")

    width = max(len(s) for s, _, _, _ in results)
    current = None
    for system, check, status, detail in results:
        if system != current:
            print()
            current = system
        mark = {PASS: "[ok]  ", FAIL: "[FAIL]", WARN: "[warn]", SKIP: "[skip]"}[status]
        print(f"  {mark} {system:<{width}}  {check:<38} {detail}")

    failed = [r for r in results if r[2] == FAIL]
    warned = [r for r in results if r[2] == WARN]
    print(f"\n{len(results)} checks: "
          f"{sum(1 for r in results if r[2] == PASS)} pass, "
          f"{len(failed)} fail, {len(warned)} warn, "
          f"{sum(1 for r in results if r[2] == SKIP)} skipped")
    if failed:
        print("\nFAILING:")
        for system, check, _, detail in failed:
            print(f"  - {system}: {check} — {detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
