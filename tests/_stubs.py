"""Shared test doubles — a deterministic LLM stub used anywhere the test
suite needs to verify control flow (agent graph, chat REPL, chat websocket)
without a real API key or network call."""

from __future__ import annotations


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, messages, tools=None):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": tools})
        if not self.responses:
            raise AssertionError("ScriptedLLM ran out of scripted responses")
        return self.responses.pop(0)


def text_resp(text):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def tool_resp(call_id, name, input_):
    return {"content": [{"type": "tool_use", "id": call_id, "name": name, "input": input_}], "stop_reason": "tool_use"}
