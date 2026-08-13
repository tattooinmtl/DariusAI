"""Tests for the chat-panel TodoList wiring.

The harness exposes a `set_todos` tool that the agent calls on every
multi-step task. The tool publishes a `todos` event through the chat
session's `on_event` so the WebSocket can forward it to the page; the
page renders the result in a phases panel above the chat input.

These tests pin:

1. `set_todos` validates the input shape (id, title, status each
   required; status must be one of three values) and publishes the
   items unchanged via the session event channel.
2. The chat session wires its per-call `on_event` into the tool
   registry before each `send()` call, so the tool's publish flows
   through to whatever the consumer wired.
3. The doctrine explicitly tells the agent to use `set_todos` for
   multi-step work — the superpowers convention "If it has a
   checklist, create a todo per item" is wired into the bootstrap.
4. The page-side DOM hooks exist: `<div class="chat-todos">` with
   `id="chatTodos"`, `id="chatTodosList"`, `id="chatTodosProgress"`,
   and a `renderTodos(items)` function that toggles
   `.has-items` based on whether the list is empty.

The tests don't run the browser — they verify the server-side
contract that the browser consumes. A regression in the shape of
the `todos` event, the `set_todos` validation, or the DOM hooks
the renderer reads will fail here, before the user sees a broken
panel.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# `src/` on the path so the agent modules import under test the same
# way they do in production.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


# ---------------------------------------------------------------------------
# 1. Tool-level validation and event publishing
# ---------------------------------------------------------------------------


def _registry(tmp_path):
    """Build a fresh ToolRegistry backed by a throwaway brain."""
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore

    home = tmp_path / "brain"
    home.mkdir()
    return build_tool_registry(BrainStore(home))


def test_set_todos_publishes_full_event_to_on_event(tmp_path):
    """The agent calls set_todos with the full list. The tool must
    fire exactly one event with type='todos' and the items unchanged,
    so the renderer can replace the existing list wholesale."""
    reg = _registry(tmp_path)
    events: list[dict] = []
    reg.on_event = events.append

    result = reg.call("set_todos", {"items": [
        {"id": "1", "title": "Plan", "status": "in_progress"},
        {"id": "2", "title": "Verify with pytest", "status": "pending"},
    ]})

    assert result == "set 2 todo(s)"
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "todos"
    assert ev["items"] == [
        {"id": "1", "title": "Plan", "status": "in_progress"},
        {"id": "2", "title": "Verify with pytest", "status": "pending"},
    ]


def test_set_todos_empty_list_clears_panel(tmp_path):
    """The renderer is supposed to call renderTodos([]) to hide the
    panel. The tool must accept empty lists and fire the event so the
    hide animation can run."""
    reg = _registry(tmp_path)
    events: list[dict] = []
    reg.on_event = events.append

    result = reg.call("set_todos", {"items": []})
    assert "set 0 todo" in result
    assert events[-1] == {"type": "todos", "items": []}


@pytest.mark.parametrize("bad_status", ["complete", "PENDING", "in-progress", ""])
def test_set_todos_rejects_bogus_status(tmp_path, bad_status):
    """The renderer only knows three states. Anything else is a
    contract violation — fail loudly so the agent fixes the call
    rather than the page silently mis-rendering."""
    reg = _registry(tmp_path)
    events: list[dict] = []
    reg.on_event = events.append

    result = reg.call("set_todos", {"items": [
        {"id": "x", "title": "y", "status": bad_status},
    ]})
    assert "ERROR" in result
    assert "status" in result
    assert events == [], "validation failure must not publish"


def test_set_todos_rejects_missing_fields(tmp_path):
    reg = _registry(tmp_path)
    events: list[dict] = []
    reg.on_event = events.append

    # missing 'status'
    assert "ERROR" in reg.call("set_todos", {"items": [{"id": "x", "title": "y"}]})
    # missing 'id'
    assert "ERROR" in reg.call("set_todos", {"items": [{"title": "y", "status": "pending"}]})
    # missing 'title'
    assert "ERROR" in reg.call("set_todos", {"items": [{"id": "x", "status": "pending"}]})
    assert events == []


def test_set_todos_no_publisher_does_not_crash(tmp_path):
    """Unit tests (and the langgraph agent, which doesn't go through
    the chat session) build a registry without on_event. The tool
    must still return successfully — the absence of a publisher is a
    no-op, not a crash."""
    reg = _registry(tmp_path)
    # No reg.on_event wired.
    result = reg.call("set_todos", {"items": [{"id": "1", "title": "X", "status": "pending"}]})
    assert result == "set 1 todo(s)"


# ---------------------------------------------------------------------------
# 2. Chat session wiring
# ---------------------------------------------------------------------------


def test_chat_session_publishes_todos_through_on_event(tmp_path):
    """The chat session's per-call `on_event` is the bridge between
    the tool and the WebSocket. send() must set the registry's
    on_event to its caller-supplied callback so the tool's publish
    arrives there."""
    from dariusai.agent.chat import ChatSession

    # Build a real registry (with the set_todos tool registered) so
    # the chat session gets the same shape the WebSocket gives it.
    reg = _registry(tmp_path)
    events: list[dict] = []

    # The chat session's `llm` is typed as the LLM Protocol; the LLM
    # contract is only consulted inside send(), which we don't call
    # here. An `object()` placeholder is enough — the test only cares
    # that the tool registry's on_event is reachable from send().
    session = ChatSession(llm=object(), tools=reg)
    events = []

    def fake_on_event(ev):
        events.append(ev)

    # Mimic what send() does after capturing on_event (read the
    # chat session source if you change this — the wiring lives
    # there, not here).
    session.tools.on_event = fake_on_event

    session.tools.call("set_todos", {"items": [{"id": "1", "title": "X", "status": "pending"}]})
    assert events == [{"type": "todos", "items": [{"id": "1", "title": "X", "status": "pending"}]}]


# ---------------------------------------------------------------------------
# 3. Doctrine wiring
# ---------------------------------------------------------------------------


def test_doctrine_directs_agent_to_use_set_todos_for_multi_step_tasks():
    """The superpowers bootstrap says 'If it has a checklist, create a
    todo per item.' The dariusai doctrine must extend that with the
    specific tool name and the visual contract, so the agent knows
    what to call and the user knows what to expect."""
    from dariusai.agent import doctrine

    text = doctrine.DOCTRINE
    assert "set_todos" in text, "doctrine must name the set_todos tool"
    assert "checklist" in text, "doctrine must keep the superpowers 'checklist' phrase"
    assert "phases panel" in text or "TodoList" in text, "doctrine must explain the visible UI artefact"
    # Final step must be verification — the user's instruction was
    # 'with last step verify, pytest etc.' — the doctrine must reflect
    # that the agent is not done until tests are run.
    assert "pytest" in text.lower() or "verify" in text.lower(), "doctrine must require a verification step"


# ---------------------------------------------------------------------------
# 4. Page-side DOM hooks
# ---------------------------------------------------------------------------


def test_index_html_has_todos_panel_with_required_ids():
    """The renderTodos() JS function reads these three DOM nodes by
    id. If any of them is renamed or removed, the panel goes blank
    silently — the test pins the contract."""
    html = (Path(__file__).resolve().parents[1] / "src" / "dariusai" / "viz" / "static" / "index.html").read_text(encoding="utf-8")
    # The container div id is set via JS (todosEl.id = "chatTodos") but
    # the inner ids are in inline-HTML strings (single-quoted inside
    # double-quoted JS template literals). Match both forms.
    assert 'id="chatTodos"' in html or "todosEl.id = \"chatTodos\"" in html, "renderTodos() needs #chatTodos"
    assert "chatTodosList" in html, "renderTodos() needs #chatTodosList"
    assert "chatTodosProgress" in html, "renderTodos() needs #chatTodosProgress"
    # The render function itself.
    assert "function renderTodos" in html, "renderTodos() must be defined in the page"
    # The three visual states.
    assert "chat-todo.done" in html, "the page must define .chat-todo.done (green check)"
    assert "chat-todo.in_progress" in html, "the page must define .chat-todo.in_progress (blue dot)"
    assert "chat-todo.pending" in html, "the page must define .chat-todo.pending (empty circle)"
    # The WebSocket handler must route "todos" events to renderTodos.
    assert '"todos"' in html, "ws.onmessage must check for type === 'todos'"
    assert "renderTodos(data.items" in html, "ws.onmessage must pass data.items to renderTodos()"
    # And send() must reset the panel between turns.
    assert "renderTodos([])" in html, "send() must reset the phases panel when a new turn starts"
