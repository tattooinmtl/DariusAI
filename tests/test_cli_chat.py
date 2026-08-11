import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dariusai.agent.chat import ChatSession
from dariusai.agent.tools import build_tool_registry
from dariusai.brain.store import BrainStore
from dariusai.cli import run_repl

from _stubs import ScriptedLLM, text_resp, tool_resp


def make_session(tmp_path, responses):
    store = BrainStore(tmp_path / "brain")
    tools = build_tool_registry(store)
    llm = ScriptedLLM(responses)
    return ChatSession(llm=llm, tools=tools), llm


def fake_io(inputs):
    it = iter(inputs)
    printed = []

    def input_fn(prompt):
        return next(it)

    def print_fn(text):
        printed.append(text)

    return input_fn, print_fn, printed


def test_repl_exits_on_slash_exit(tmp_path):
    session, llm = make_session(tmp_path, [])
    input_fn, print_fn, printed = fake_io(["/exit"])
    run_repl(session, input_fn=input_fn, print_fn=print_fn)
    assert llm.calls == []  # no message ever sent to the model
    assert any("type a message" in p for p in printed)


def test_repl_exits_on_eof(tmp_path):
    session, llm = make_session(tmp_path, [])
    def input_fn(prompt):
        raise EOFError
    run_repl(session, input_fn=input_fn, print_fn=lambda t: None)
    assert llm.calls == []


def test_repl_sends_message_and_prints_reply(tmp_path):
    session, llm = make_session(tmp_path, [text_resp("hi there")])
    input_fn, print_fn, printed = fake_io(["hello", "/exit"])
    run_repl(session, input_fn=input_fn, print_fn=print_fn)
    assert len(llm.calls) == 1
    assert any("hi there" in p for p in printed)


def test_repl_prints_tool_activity(tmp_path):
    session, llm = make_session(tmp_path, [
        tool_resp("call-1", "list_dir", {"path": str(tmp_path)}),
        text_resp("done listing"),
    ])
    input_fn, print_fn, printed = fake_io(["list the dir", "/exit"])
    run_repl(session, input_fn=input_fn, print_fn=print_fn)
    assert any("[tool] list_dir" in p for p in printed)
    assert any("[result]" in p for p in printed)
    assert any("done listing" in p for p in printed)


def test_repl_conversation_persists_across_turns(tmp_path):
    session, llm = make_session(tmp_path, [text_resp("first reply"), text_resp("second reply")])
    input_fn, print_fn, printed = fake_io(["first message", "second message", "/exit"])
    run_repl(session, input_fn=input_fn, print_fn=print_fn)
    assert len(llm.calls) == 2
    # second call's message history includes the first turn's user + assistant messages
    second_call_messages = llm.calls[1]["messages"]
    assert any(m["role"] == "user" and m["content"] == "first message" for m in second_call_messages)
    assert any(m["role"] == "assistant" for m in second_call_messages)


def test_repl_survives_llm_exception_and_keeps_looping(tmp_path):
    class ExplodingLLM:
        def complete(self, system, messages, tools=None):
            raise RuntimeError("simulated API failure")

    store = BrainStore(tmp_path / "brain")
    tools = build_tool_registry(store)
    session = ChatSession(llm=ExplodingLLM(), tools=tools)
    input_fn, print_fn, printed = fake_io(["this will fail", "/exit"])
    run_repl(session, input_fn=input_fn, print_fn=print_fn)  # must not raise
    assert any("simulated API failure" in p for p in printed)


def test_empty_input_does_not_call_llm(tmp_path):
    session, llm = make_session(tmp_path, [text_resp("reply")])
    input_fn, print_fn, printed = fake_io(["", "   ", "real message", "/exit"])
    run_repl(session, input_fn=input_fn, print_fn=print_fn)
    assert len(llm.calls) == 1
