import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent.graph import run_agent
from dariusai.agent.tools import build_tool_registry
from dariusai.brain.store import BrainStore

from _stubs import ScriptedLLM, text_resp, tool_resp


def test_simple_task_no_tools_no_tests(tmp_path):
    store = BrainStore(tmp_path / "brain")
    tools = build_tool_registry(store)
    llm = ScriptedLLM([
        text_resp("1. just say hi"),          # planner
        text_resp("done, said hi"),            # coder — no tool calls, signals completion
    ])
    result = run_agent("say hi", llm, tools, project_dir=tmp_path / "project")
    assert result["done"] is True
    assert result["verified"] is True
    assert "no tests ran" in result["verdict"]
    assert len(llm.calls) == 2


def test_coder_calls_a_real_tool_before_finishing(tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "a.txt").write_text("hello")
    store = BrainStore(tmp_path / "brain")
    tools = build_tool_registry(store)
    llm = ScriptedLLM([
        text_resp("1. look around, 2. report"),                              # planner
        tool_resp("call-1", "list_dir", {"path": str(tmp_path / "project")}),  # coder: use a tool
        text_resp("found a.txt, done"),                                      # coder: finish
    ])
    result = run_agent("list the project files", llm, tools, project_dir=tmp_path / "project")
    assert result["done"] is True
    # the tool result actually made it back into the conversation the model saw
    tool_result_messages = [
        m for m in result["messages"] if m["role"] == "user" and isinstance(m["content"], list)
    ]
    assert any("a.txt" in tr["content"] for m in tool_result_messages for tr in m["content"])


def test_failed_tests_trigger_retry_then_gives_up(tmp_path):
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_always_fails.py").write_text("def test_fail():\n    assert False\n")
    store = BrainStore(tmp_path / "brain")
    tools = build_tool_registry(store)
    llm = ScriptedLLM([
        text_resp("1. run the tests"),   # planner
        text_resp("ran it, should pass"),  # coder attempt 1
        text_resp("tried again"),          # coder attempt 2 (after first verify failure)
    ])
    result = run_agent("make the tests pass", llm, tools, project_dir=project, max_retries=2)
    assert result["done"] is True
    assert result["verified"] is False
    assert result["verdict"] == "tests failed"
    assert result["retries"] == 2
    assert "exit=1" in result["test_output"] or "exit=" in result["test_output"]
    # coder ran twice (initial + one retry), planner once — confirms the loop-back edge fired
    assert len(llm.calls) == 3


def test_learn_skill_flow_reachable_from_coder_loop(tmp_path):
    """Not a live web-search test (that's covered elsewhere) — just proves
    the graph correctly routes a tool_use call for learn_skill through the
    real ToolRegistry and the result lands back in the brain."""
    store = BrainStore(tmp_path / "brain")
    tools = build_tool_registry(store)
    sources = [{"url": f"https://site{i}.com/x", "quote": f"q{i}"} for i in range(5)]
    llm = ScriptedLLM([
        text_resp("1. learn something new"),
        tool_resp("call-1", "learn_skill", {
            "title": "Learned via graph", "problem": "p", "solution": "s",
            "code_examples": "c", "best_practices": "b", "edge_cases": "e",
            "sources": sources,
        }),
        text_resp("filed the skill, done"),
    ])
    result = run_agent("learn something", llm, tools, project_dir=tmp_path / "empty")
    assert result["done"] is True
    assert store.search("Learned via graph")
