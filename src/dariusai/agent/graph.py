"""The LangGraph orchestrator — project spec's own architecture diagram:
Planner -> Coder -> Tester -> Verifier -> Memory Writer, looping back to
Coder on a failed verification (bounded by max_retries so a stuck task
can't loop forever).

- Planner: asks the model for a short plan, seeds the Coder's conversation.
- Coder: a real tool-calling loop (bound to the ToolRegistry) — reads/writes
  files, runs shell commands, and when it doesn't know something, calls
  web_research then learn_skill to file a new brain node before continuing.
- Tester: runs whatever test command the project actually has.
- Verifier: passes/fails based on the test run (or trusts the Coder's own
  completion signal when there was nothing to test).
- Memory Writer: publishes the session outcome to the activity bus (so it
  pulses in the viz) and ends the run.

The `llm` argument is an LLM protocol (agent/llm.py) — production code
passes AnthropicLLM(), tests pass a deterministic stub, so this file's
control flow is fully testable without network access or an API key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from ..brain.store import COORDINATOR_ID
from ..events.bus import bus
from .doctrine import with_doctrine
from .llm import LLM
from .tools import ToolRegistry

MAX_TOOL_ITERATIONS = 12


class AgentState(TypedDict, total=False):
    task: str
    plan: str
    messages: list[dict[str, Any]]
    tool_iterations: int
    coder_summary: str
    test_output: str
    tests_ran: bool
    verified: bool
    verdict: str
    retries: int
    done: bool


PLANNER_SYSTEM = with_doctrine(
    "Break the task into a short numbered plan (3-6 steps), plain text, no preamble. "
    "Plan only the steps the task actually needs."
)

CODER_SYSTEM = with_doctrine(
    "You are the Coder in a self-improving coding agent. Solve the task using the tools available. "
    "When the task is actually done, reply with plain text and no further tool calls."
)


def _text_of(content: list[dict[str, Any]]) -> str:
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


def planner_node(state: AgentState, llm: LLM) -> AgentState:
    resp = llm.complete(system=PLANNER_SYSTEM, messages=[{"role": "user", "content": state["task"]}])
    plan = _text_of(resp["content"])
    state["plan"] = plan
    state["messages"] = [{"role": "user", "content": f"Task: {state['task']}\n\nPlan:\n{plan}"}]
    return state


def coder_node(state: AgentState, llm: LLM, tools: ToolRegistry) -> AgentState:
    messages = state.get("messages", [])
    iterations = state.get("tool_iterations", 0)
    tool_schemas = tools.to_anthropic_tools()

    for _ in range(MAX_TOOL_ITERATIONS):
        resp = llm.complete(system=CODER_SYSTEM, messages=messages, tools=tool_schemas)
        messages.append({"role": "assistant", "content": resp["content"]})
        tool_uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
        iterations += 1
        if not tool_uses:
            state["coder_summary"] = _text_of(resp["content"])
            break
        results = [
            {"type": "tool_result", "tool_use_id": call["id"], "content": tools.call(call["name"], call["input"])}
            for call in tool_uses
        ]
        messages.append({"role": "user", "content": results})
        if iterations >= MAX_TOOL_ITERATIONS:
            state["coder_summary"] = "hit the tool-call iteration cap without signaling completion."

    state["messages"] = messages
    state["tool_iterations"] = iterations
    return state


def _detect_test_command(project_dir: Path) -> str | None:
    if (project_dir / "pytest.ini").exists() or (project_dir / "pyproject.toml").exists() or (project_dir / "tests").is_dir():
        return "python -m pytest -q"
    if (project_dir / "package.json").exists():
        return "npm test --silent"
    if (project_dir / "go.mod").exists():
        return "go test ./..."
    if (project_dir / "Cargo.toml").exists():
        return "cargo test"
    return None


def tester_node(state: AgentState, tools: ToolRegistry, project_dir: Path) -> AgentState:
    cmd = _detect_test_command(project_dir)
    if cmd is None:
        state["test_output"] = "no test command detected for this project — skipped"
        state["tests_ran"] = False
        return state
    state["test_output"] = tools.call("run_shell", {"command": cmd, "cwd": str(project_dir)})
    state["tests_ran"] = True
    return state


def verifier_node(state: AgentState, llm: LLM) -> AgentState:
    if not state.get("tests_ran"):
        state["verified"] = True
        state["verdict"] = "no tests ran; accepted the Coder's own completion signal"
        return state
    passed = "exit=0" in state.get("test_output", "")
    state["verified"] = passed
    state["verdict"] = "tests passed" if passed else "tests failed"
    if not passed:
        state["retries"] = state.get("retries", 0) + 1
    return state


def memory_writer_node(state: AgentState) -> AgentState:
    bus.publish({
        "kind": "session_complete", "route": COORDINATOR_ID,
        "task": state.get("task", ""), "verdict": state.get("verdict", ""),
    })
    state["done"] = True
    return state


def build_graph(llm: LLM, tools: ToolRegistry, project_dir: Path | str = ".", max_retries: int = 2):
    project_dir = Path(project_dir)
    graph = StateGraph(AgentState)
    graph.add_node("planner", lambda s: planner_node(s, llm))
    graph.add_node("coder", lambda s: coder_node(s, llm, tools))
    graph.add_node("tester", lambda s: tester_node(s, tools, project_dir))
    graph.add_node("verifier", lambda s: verifier_node(s, llm))
    graph.add_node("memory_writer", memory_writer_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "coder")
    graph.add_edge("coder", "tester")
    graph.add_edge("tester", "verifier")

    def route_after_verify(state: AgentState) -> str:
        if state.get("verified"):
            return "memory_writer"
        if state.get("retries", 0) >= max_retries:
            return "memory_writer"
        return "coder"

    graph.add_conditional_edges("verifier", route_after_verify, {"coder": "coder", "memory_writer": "memory_writer"})
    graph.add_edge("memory_writer", END)
    return graph.compile()


def run_agent(task: str, llm: LLM, tools: ToolRegistry, project_dir: Path | str = ".", max_retries: int = 2) -> AgentState:
    app = build_graph(llm, tools, project_dir=project_dir, max_retries=max_retries)
    return app.invoke({"task": task})
