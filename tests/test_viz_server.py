import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient

from dariusai.brain.skill import Skill, Source
from dariusai.brain.store import COORDINATOR_ID, BrainStore
from dariusai.viz.server import create_app


def make_client(tmp_path):
    app = create_app(tmp_path)
    return app.state.store, TestClient(app)


def test_index_serves_html(tmp_path):
    _, client = make_client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "DariusAI" in r.text


def test_graph_endpoint_has_coordinator(tmp_path):
    _, client = make_client(tmp_path)
    r = client.get("/api/graph")
    assert r.status_code == 200
    payload = r.json()
    ids = {n["id"] for n in payload["nodes"]}
    assert COORDINATOR_ID in ids


def test_get_node_full_content(tmp_path):
    store, client = make_client(tmp_path)
    skill = store.add_skill(Skill(
        id="skill-x", title="Test skill", problem="p", solution="s",
        code_examples="c", best_practices="b", edge_cases="e",
        sources=[Source(url="https://a.com", quote="q1"), Source(url="https://b.com", quote="q2")],
    ))
    r = client.get(f"/api/node/{skill.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Test skill"
    assert len(body["sources"]) == 2


def test_get_node_404_for_unknown(tmp_path):
    _, client = make_client(tmp_path)
    r = client.get("/api/node/does-not-exist")
    assert r.status_code == 404


def test_get_coordinator_node_rejected(tmp_path):
    _, client = make_client(tmp_path)
    r = client.get(f"/api/node/{COORDINATOR_ID}")
    assert r.status_code == 400


def test_put_edits_existing_node(tmp_path):
    store, client = make_client(tmp_path)
    skill = store.add_skill(Skill(id="skill-y", title="Old title", problem="old"))
    r = client.put(f"/api/node/{skill.id}", json={"title": "New title", "problem": "new problem"})
    assert r.status_code == 200
    assert r.json()["title"] == "New title"
    fetched = store.get_skill(skill.id)
    assert fetched.title == "New title"
    assert fetched.problem == "new problem"


def test_delete_removes_node(tmp_path):
    store, client = make_client(tmp_path)
    skill = store.add_skill(Skill(id="skill-z", title="Doomed"))
    r = client.delete(f"/api/node/{skill.id}")
    assert r.status_code == 200
    r2 = client.get(f"/api/node/{skill.id}")
    assert r2.status_code == 404


def test_websocket_replays_recent_and_streams_live(tmp_path):
    from dariusai.events.bus import bus
    bus.clear()  # `bus` is a process-wide singleton; isolate from other tests' history
    store, client = make_client(tmp_path)
    with client.websocket_connect("/ws/events") as ws:
        skill = store.add_skill(Skill(id="skill-live", title="Live one"))
        bus.publish({"kind": "skill_learned", "id": skill.id, "label": skill.title})
        msg = ws.receive_json()
        assert msg["kind"] == "skill_learned"
        assert msg["id"] == "skill-live"


def test_save_learned_skill_publishes_to_bus(tmp_path):
    from dariusai.brain.learn import save_learned_skill
    from dariusai.brain.skill import Source
    from dariusai.events.bus import bus

    bus.clear()
    store = BrainStore(tmp_path)
    sources = [
        Source(url="https://a.com/x", quote="q1"), Source(url="https://b.com/x", quote="q2"),
        Source(url="https://c.com/x", quote="q3"), Source(url="https://d.com/x", quote="q4"),
        Source(url="https://e.com/x", quote="q5"),
    ]
    skill = save_learned_skill(
        store, title="Auto-learned thing", problem="p", solution="s",
        code_examples="c", best_practices="b", edge_cases="e", sources=sources,
    )
    recent = bus.recent()
    assert any(e["kind"] == "skill_learned" and e["id"] == skill.id for e in recent)
