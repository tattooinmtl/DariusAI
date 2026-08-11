import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from dariusai.brain.skill import Skill
from dariusai.viz.server import create_app

from _stubs import ScriptedLLM, text_resp, tool_resp


def make_client(tmp_path, responses):
    llm = ScriptedLLM(responses)
    app = create_app(tmp_path / "brain", project_dir=tmp_path, llm=llm)
    return TestClient(app), llm


def test_chat_simple_reply(tmp_path):
    client, llm = make_client(tmp_path, [text_resp("hello back")])
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text("hi")
        msg = ws.receive_json()
        assert msg == {"type": "assistant_text", "text": "hello back"}


def test_chat_streams_tool_activity(tmp_path):
    client, llm = make_client(tmp_path, [
        tool_resp("call-1", "list_dir", {"path": str(tmp_path)}),
        text_resp("done"),
    ])
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text("list files")
        msgs = [ws.receive_json() for _ in range(3)]
    types = [m["type"] for m in msgs]
    assert types == ["tool_call_start", "tool_call_result", "assistant_text"]
    assert msgs[0]["name"] == "list_dir"


def test_chat_conversation_persists_within_connection(tmp_path):
    client, llm = make_client(tmp_path, [text_resp("first"), text_resp("second")])
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text("turn one")
        ws.receive_json()
        ws.send_text("turn two")
        ws.receive_json()
    second_call_messages = llm.calls[1]["messages"]
    assert any(m["role"] == "user" and m["content"] == "turn one" for m in second_call_messages)


def test_chat_llm_exception_sends_error_and_does_not_hang(tmp_path):
    class ExplodingLLM:
        calls = 0

        def complete(self, system, messages, tools=None):
            raise RuntimeError("simulated API failure")

    app = create_app(tmp_path / "brain", project_dir=tmp_path, llm=ExplodingLLM())
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text("this will fail")
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "simulated API failure" in msg["message"]
        # connection is still alive and usable for a next turn, not hung/dead
        ws.send_text("still there?")
        msg2 = ws.receive_json()
        assert msg2["type"] == "error"


def test_chat_without_llm_configured_sends_error(tmp_path, monkeypatch):
    # AnthropicLLM() doesn't actually raise just because ANTHROPIC_API_KEY is
    # unset (the SDK only fails at request time) — so exercise the "LLM
    # unavailable" branch the way it can really happen: construction itself
    # failing (missing dependency, bad config, etc.).
    import dariusai.agent.llm as llm_module

    class BrokenLLM:
        def __init__(self, *a, **k):
            raise RuntimeError("no credentials configured")

        @classmethod
        def from_store(cls, store, **k):
            return cls()

    monkeypatch.setattr(llm_module, "AnthropicLLM", BrokenLLM)

    app = create_app(tmp_path / "brain", project_dir=tmp_path, llm=None)
    client = TestClient(app)
    with client.websocket_connect("/ws/chat") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "no credentials configured" in msg["message"]


def test_chat_turn_is_indexed_as_conversation_with_links(tmp_path):
    from dariusai.events.bus import bus

    bus.clear()
    app = create_app(tmp_path / "brain", project_dir=tmp_path, llm=ScriptedLLM([
        text_resp("Use USGS feed https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson "
                  "and add map markers from each feature."),
    ]))
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text("USGS API calls for live earthquakes and map markers")
        msg = ws.receive_json()
        assert msg["type"] == "assistant_text"

    hits = client.get("/api/search", params={"q": "usgs", "limit": 20}).json()
    conv_hits = [h for h in hits if h["category"] == "conversation"]
    assert conv_hits, "expected an auto-indexed conversation node"

    node_id = conv_hits[0]["id"]
    node = client.get(f"/api/node/{node_id}").json()
    assert node["category"] == "conversation"
    assert "USGS" in node["title"].upper()
    assert any("usgs" in t.lower() for t in node["tags"])
    assert any("earthquake.usgs.gov" in s["url"] for s in node["sources"])

    with client.websocket_connect("/ws/events") as ws:
        # replay should include the turn's activity events
        seen = [ws.receive_json() for _ in range(4)]
    assert any(e.get("kind") == "conversation_logged" and e.get("id") == node_id for e in seen)


def test_conversation_node_branches_from_okf_when_present(tmp_path):
    from dariusai.events.bus import bus

    bus.clear()
    app = create_app(tmp_path / "brain", project_dir=tmp_path, llm=ScriptedLLM([
        text_resp("Use USGS feed links and marker rendering."),
    ]))
    app.state.store.add_skill(Skill(
        id="omni-okf-knowledge",
        title="OKF Knowledge Root",
        category="skill",
        tags=["okf"],
        problem="Anchor node",
        solution="Root knowledge",
    ))
    client = TestClient(app)

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_text("How do we add USGS markers on OpenStreetMap?")
        ws.receive_json()

    hits = client.get("/api/search", params={"q": "usgs", "limit": 20}).json()
    conv_hits = [h for h in hits if h["category"] == "conversation"]
    assert conv_hits
    node_id = conv_hits[0]["id"]

    graph = client.get("/api/graph").json()
    assert any(
        e["source"] == node_id and e["target"] == "omni-okf-knowledge" and e["kind"] == "related"
        for e in graph["edges"]
    )

    with client.websocket_connect("/ws/events") as ws:
        seen = [ws.receive_json() for _ in range(5)]
    evt = next(e for e in seen if e.get("kind") == "conversation_logged" and e.get("id") == node_id)
    assert evt.get("source") == "omni-okf-knowledge"
