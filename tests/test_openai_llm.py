"""The OpenAI-shaped client and the protocol routing that picks it.

The bug this covers: every provider the project targets except Anthropic
speaks `POST {root}/chat/completions`, but the only client posted to
`{base_url}/v1/messages`. That produced a 404 that looked like a bad URL
and was really a bad protocol.

Offline — the HTTP call is injected.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.agent.llm import build_llm
from dariusai.agent.openai_llm import OpenAILLM, explain_provider_error
from dariusai.brain.store import BrainStore


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        return self._payload


def transport(payload, status=200, captured=None):
    def _post(url, headers, body):
        if captured is not None:
            captured.update(url=url, headers=headers, body=body)
        return FakeResponse(payload, status)
    return _post


def reply(text=None, tool_calls=None, finish="stop"):
    message = {"content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message, "finish_reason": finish}]}


# ---- it posts where these providers actually listen ----------------------

def test_posts_to_chat_completions_not_v1_messages():
    seen = {}
    OpenAILLM(model="m", api_key="k", base_url="https://host.test/v1",
              transport=transport(reply("hi"), captured=seen)).complete("sys", [{"role": "user", "content": "hi"}])
    assert seen["url"] == "https://host.test/v1/chat/completions"


def test_a_pasted_endpoint_url_is_normalised():
    """The URL that was actually saved in Settings — the one from NVIDIA's
    quickstart — must not become .../chat/completions/chat/completions."""
    seen = {}
    OpenAILLM(model="m", api_key="k",
              base_url="https://integrate.api.nvidia.com/v1/chat/completions",
              transport=transport(reply("hi"), captured=seen)).complete("", [{"role": "user", "content": "x"}])
    assert seen["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"


def test_bearer_auth_and_model_in_body():
    seen = {}
    OpenAILLM(model="meta/llama", api_key="key1", base_url="https://h/v1",
              transport=transport(reply("x"), captured=seen)).complete("", [{"role": "user", "content": "x"}])
    assert seen["headers"]["Authorization"] == "Bearer key1"
    assert seen["body"]["model"] == "meta/llama"


def test_a_missing_model_fails_with_a_useful_message():
    with pytest.raises(ValueError, match="pick one in Settings"):
        OpenAILLM(model="", api_key="k", base_url="https://h/v1")


# ---- translation out ------------------------------------------------------

def test_system_prompt_becomes_a_system_message():
    seen = {}
    OpenAILLM(model="m", api_key="k", base_url="https://h/v1",
              transport=transport(reply("x"), captured=seen)).complete("BE BRIEF", [{"role": "user", "content": "hi"}])
    assert seen["body"]["messages"][0] == {"role": "system", "content": "BE BRIEF"}


def test_tools_are_converted_to_function_schema():
    seen = {}
    tools = [{"name": "read_file", "description": "read it",
              "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}]
    OpenAILLM(model="m", api_key="k", base_url="https://h/v1",
              transport=transport(reply("x"), captured=seen)).complete("", [{"role": "user", "content": "hi"}], tools)
    fn = seen["body"]["tools"][0]
    assert fn["type"] == "function"
    assert fn["function"]["name"] == "read_file"
    assert fn["function"]["parameters"]["properties"]["path"]["type"] == "string"


def test_assistant_tool_use_blocks_become_tool_calls():
    seen = {}
    history = [
        {"role": "user", "content": "read it"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "sure"},
            {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "a.txt"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "file body"},
        ]},
    ]
    OpenAILLM(model="m", api_key="k", base_url="https://h/v1",
              transport=transport(reply("done"), captured=seen)).complete("", history)

    msgs = seen["body"]["messages"]
    assistant = next(m for m in msgs if m["role"] == "assistant")
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}

    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["content"] == "file body"


# ---- translation back -----------------------------------------------------

def test_plain_text_reply_becomes_a_text_block():
    out = OpenAILLM(model="m", api_key="k", base_url="https://h/v1",
                    transport=transport(reply("pong"))).complete("", [{"role": "user", "content": "ping"}])
    assert out["content"] == [{"type": "text", "text": "pong"}]
    assert out["stop_reason"] == "end_turn"


def test_tool_calls_become_tool_use_blocks():
    calls = [{"id": "c1", "type": "function",
              "function": {"name": "run_shell", "arguments": '{"command": "ls"}'}}]
    out = OpenAILLM(model="m", api_key="k", base_url="https://h/v1",
                    transport=transport(reply(None, calls, finish="tool_calls"))).complete("", [{"role": "user", "content": "x"}])
    block = out["content"][0]
    assert block["type"] == "tool_use"
    assert block["name"] == "run_shell"
    assert block["input"] == {"command": "ls"}
    assert out["stop_reason"] == "tool_use"          # keeps the agent loop going


def test_malformed_tool_arguments_do_not_crash_the_loop():
    calls = [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{not json"}}]
    out = OpenAILLM(model="m", api_key="k", base_url="https://h/v1",
                    transport=transport(reply(None, calls))).complete("", [{"role": "user", "content": "x"}])
    assert out["content"][0]["input"]["__malformed_arguments__"] == "{not json"


# ---- errors people can act on --------------------------------------------

def test_model_not_enabled_for_account_is_explained():
    """NVIDIA's real reply when the catalogue lists a model your account
    can't call. Verbatim from a live run."""
    detail = '{"status": 404, "detail": "Function \'6497fc2b\': Not found for account \'abc\'"}'
    msg = explain_provider_error(404, detail, "ai21labs/jamba", "https://h/v1/chat/completions")
    assert "isn't enabled for your account" in msg
    assert "pick a different one" in msg
    assert "404" in msg  # raw detail is never thrown away


@pytest.mark.parametrize("status,detail,expected", [
    (401, "unauthorized", "API key was rejected"),
    (429, "rate limit exceeded", "rate-limiting"),
    (503, "upstream unavailable", "failing on its side"),
    (404, "no route", "Base URL"),
])
def test_common_failures_say_what_to_do(status, detail, expected):
    assert expected in explain_provider_error(status, detail, "m", "https://h/v1/chat/completions")


# ---- routing --------------------------------------------------------------

def test_openai_shaped_provider_gets_the_openai_client(tmp_path):
    store = BrainStore(tmp_path / "brain")
    store.upsert_provider("nvidia", base_url="https://integrate.api.nvidia.com/v1",
                          model="meta/llama-3.1-8b-instruct", api_key="k")
    store.set_active_provider("nvidia")
    assert type(build_llm(store)).__name__ == "OpenAILLM"


def test_anthropic_provider_still_gets_the_anthropic_client(tmp_path, monkeypatch):
    store = BrainStore(tmp_path / "brain")
    store.upsert_provider("anthropic", base_url="", model="claude-sonnet-5", api_key="k")
    store.set_active_provider("anthropic")
    assert type(build_llm(store)).__name__ == "AnthropicLLM"


def test_an_unknown_provider_is_assumed_openai_compatible(tmp_path):
    """Most gateways copy OpenAI's shape, so that's the right default for a
    provider this project has never heard of."""
    store = BrainStore(tmp_path / "brain")
    store.upsert_provider("some-new-gateway", base_url="https://new.test/v1", model="m", api_key="k")
    store.set_active_provider("some-new-gateway")
    assert type(build_llm(store)).__name__ == "OpenAILLM"
