import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from dariusai.agent.chat import ChatSession
from dariusai.agent.tools import build_tool_registry
from dariusai.brain.store import BrainStore
from dariusai.events.bus import bus
from dariusai.viz.server import create_app

from _stubs import ScriptedLLM, text_resp


def test_get_and_set_project_dir(tmp_path):
    other = tmp_path / "other_project"
    other.mkdir()
    app = create_app(tmp_path / "brain", project_dir=tmp_path / "initial")
    (tmp_path / "initial").mkdir()
    client = TestClient(app)

    r = client.get("/api/project-dir")
    assert r.json()["project_dir"].endswith("initial")

    r2 = client.put("/api/project-dir", json={"path": str(other)})
    assert r2.status_code == 200
    assert r2.json()["project_dir"].endswith("other_project")

    r3 = client.get("/api/project-dir")
    assert r3.json()["project_dir"].endswith("other_project")


def test_set_project_dir_rejects_nonexistent_path(tmp_path):
    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    client = TestClient(app)
    r = client.put("/api/project-dir", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_project_dir_persists_across_app_restarts(tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir()

    app = create_app(tmp_path / "brain", project_dir=tmp_path)
    client = TestClient(app)
    r = client.put("/api/project-dir", json={"path": str(selected)})
    assert r.status_code == 200

    restarted = create_app(tmp_path / "brain")
    restarted_client = TestClient(restarted)
    r2 = restarted_client.get("/api/project-dir")
    assert Path(r2.json()["project_dir"]) == selected.resolve()


def test_chat_send_publishes_turn_start_and_end(tmp_path):
    bus.clear()
    store = BrainStore(tmp_path / "brain")
    tools = build_tool_registry(store)
    session = ChatSession(llm=ScriptedLLM([text_resp("hi")]), tools=tools)
    session.send("hello")
    kinds_phases = [(e["kind"], e.get("phase")) for e in bus.recent() if e["kind"] == "agent_turn"]
    assert ("agent_turn", "start") in kinds_phases
    assert ("agent_turn", "end") in kinds_phases


def test_turn_end_published_even_if_llm_raises(tmp_path):
    bus.clear()
    store = BrainStore(tmp_path / "brain")
    tools = build_tool_registry(store)

    class ExplodingLLM:
        def complete(self, system, messages, tools=None):
            raise RuntimeError("boom")

    session = ChatSession(llm=ExplodingLLM(), tools=tools)
    try:
        session.send("hello")
    except RuntimeError:
        pass
    kinds_phases = [(e["kind"], e.get("phase")) for e in bus.recent() if e["kind"] == "agent_turn"]
    assert ("agent_turn", "end") in kinds_phases
