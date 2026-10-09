"""Phase 5 (2026-10-09): slimming and robustness.

- networkx replaced by brain_graph.lite (it cost ~0.5 s and ~20 MB a launch)
- CodeMirror language modes load on demand
- dead files out of the repo (three.js, empty neural3d.js, Voice zips, logo1)
- the chat keeps at most CHAT_DOM_MAX items in the page
- store writes are serialized; WAL journal
- WebView2 low-end device mode on small PCs
- every SKILL.md reachable, at any depth
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PAGE = (ROOT / "src/dariusai/viz/static/index.html").read_text(encoding="utf-8")


def test_networkx_is_not_imported_by_the_app():
    code = ("import sys; sys.path.insert(0, r'%s'); import dariusai.viz.server; "
            "print('networkx' in sys.modules)" % (ROOT / "src"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.stdout.strip() == "False", out.stderr[-400:]
    assert "networkx" not in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_lite_graph_behaves_like_the_networkx_slice_we_used():
    from brain_graph.lite import LiteMultiDiGraph

    g = LiteMultiDiGraph()
    g.add_node("a", label="A"); g.add_node("b"); g.add_edge("a", "b", kind="related"); g.add_edge("a", "b", kind="x")
    g.add_node("a", category="c")                               # updates, doesn't replace
    assert g.nodes["a"] == {"label": "A", "category": "c"}
    assert "a" in g and len(g) == 2 and sorted(g.nodes) == ["a", "b"]
    assert [d["kind"] for _, _, d in g.out_edges("a", data=True)] == ["related", "x"]
    assert g.in_edges("b") == [("a", "b"), ("a", "b")]
    assert set(g.get_edge_data("a", "b")) == {0, 1} and g.get_edge_data("b", "a") is None
    assert g.shortest_path("a", "b") == ["a", "b"] and g.shortest_path("b", "a") == []
    assert g.neighbors("a") == ["b"]


def test_language_modes_are_loaded_on_demand():
    assert '<script src="/vendor/codemirror/mode-python.min.js">' not in PAGE
    assert "function ensureMode(mode)" in PAGE and "applyModeWhenReady(cm, modeForLang(lang));" in PAGE


def test_dead_files_are_gone():
    for rel in ("src/dariusai/viz/static/vendor/three/three.min.js", "src/dariusai/viz/static/neural3d.js"):
        assert not (ROOT / rel).exists(), rel
    tracked = subprocess.run(["git", "ls-files", "Voice", "logo1.png"], cwd=ROOT, capture_output=True, text=True).stdout
    assert tracked.strip() == ""


def test_chat_dom_is_bounded():
    assert "var CHAT_DOM_MAX = 150, SESSION_TAIL = 60" in PAGE
    assert PAGE.count("trimChat();") >= 3


def test_store_writes_are_serialized(tmp_path):
    from dariusai.brain.skill import Skill
    from dariusai.brain.store import BrainStore

    store = BrainStore(tmp_path / "brain")
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    errors = []

    def writer(k):
        try:
            for i in range(15):
                store.add_skill(Skill(id=f"s{k}-{i}", title=f"skill {k} {i}"))
                store.touch_usage(f"s{k}-{i}")
                store.set_setting(f"k{k}", str(i))
        except Exception as exc:          # "cannot commit", "recursive use"...
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == []
    assert store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 60


def test_webview_low_memory_mode_switch():
    from dariusai.os_integration import apply_webview_memory_flags

    env = {"DARIUSAI_LOW_MEMORY": "1"}
    assert apply_webview_memory_flags(env)
    assert "--enable-low-end-device-mode" in env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"]
    assert apply_webview_memory_flags(env)                       # idempotent
    assert env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"].count("--enable-low-end-device-mode") == 1
    assert not apply_webview_memory_flags({"DARIUSAI_LOW_MEMORY": "0"})


def test_every_skill_is_reachable_at_any_depth(tmp_path):
    from dariusai.agent import tools as T
    from dariusai.brain.store import BrainStore

    store = BrainStore(tmp_path / "brain")
    store.set_setting("project_dir", str(tmp_path))
    names = {p.parent.name for p in T._skill_files(store)}
    assert {"vllm", "dspy", "dogfood", "yuanbao"} <= names       # nested and ungrouped ones
