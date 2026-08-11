"""The reusable tool-calling turn — one implementation shared by the
terminal REPL (cli.py's `dariusai chat`) and the web chat websocket
(viz/server.py's `/ws/chat`), instead of two copies of the same loop.

Unlike agent/graph.py's run_agent() (one task, full Planner/Tester/Verifier
pipeline, then done), a ChatSession is long-lived: it keeps the full
conversation across turns and just runs the tool-calling loop per user
message — the natural shape for an interactive chat, same as omni's own
agent loop.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

from ..brain.store import COORDINATOR_ID
from ..events.bus import bus
from .doctrine import with_doctrine
from .llm import LLM
from .tools import ToolRegistry

# Twelve was far too low: writing a handful of files is already five calls
# before any reading, project creation or verification, so real work hit the
# cap routinely — and it hit it *after* doing the work, which made it look
# like a crash. Overridable for anyone who wants a tighter leash.
MAX_TOOL_ITERATIONS = int(os.environ.get("DARIUSAI_MAX_TOOL_ITERATIONS", "60"))

CHAT_SYSTEM = with_doctrine(
    "You are DariusAI, a self-improving polyglot coding agent. Use the tools available to read/write "
    "files and run shell commands in any language's toolchain. "
    "For questions about external APIs, web services, frameworks, or current links/examples, call web_research first "
    "before answering. Keep replies concise."
)

EventCallback = Callable[[dict[str, Any]], None]
TurnCompleteCallback = Callable[[dict[str, Any]], None]


def _text_of(content: list[dict[str, Any]]) -> str:
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


@dataclass
class ChatSession:
    llm: LLM
    tools: ToolRegistry
    system: str = CHAT_SYSTEM
    messages: list[dict[str, Any]] = field(default_factory=list)
    on_turn_complete: TurnCompleteCallback | None = None

    def send(self, user_text: str, on_event: EventCallback | None = None) -> str:
        """Append a user message, run the tool-calling loop until the model
        replies with no further tool calls (or the iteration cap), and
        return the final assistant text. `on_event` is called for every
        intermediate step (assistant text, tool call start/result) so a
        caller can stream progress instead of waiting for the whole turn."""
        def emit(event: dict[str, Any]) -> None:
            if on_event:
                on_event(event)

        self.messages.append({"role": "user", "content": user_text})
        tool_schemas = self.tools.to_anthropic_tools()
        final_text = ""
        narration: list[str] = []
        tool_results: list[dict[str, str]] = []

        # Lets the neural view show the coordinator "thinking" for the whole
        # turn, not just the instants a tool call fires — real-time activity
        # the moment you ask the AI to do something, per the project spec.
        bus.publish({"kind": "agent_turn", "phase": "start", "route": COORDINATOR_ID})
        try:
            for i in range(MAX_TOOL_ITERATIONS):
                resp = self.llm.complete(system=self.system, messages=self.messages, tools=tool_schemas)
                self.messages.append({"role": "assistant", "content": resp["content"]})

                text = _text_of(resp["content"])
                if text:
                    emit({"type": "assistant_text", "text": text})
                    # Keep everything the model narrated along the way. The
                    # cap used to *replace* final_text with its own notice,
                    # throwing away the running commentary — and filing that
                    # notice as the conversation's record of the turn.
                    narration.append(text)

                tool_uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
                if not tool_uses:
                    final_text = text
                    break

                results = []
                for call in tool_uses:
                    emit({"type": "tool_call_start", "name": call["name"], "input": call["input"]})
                    output = self.tools.call(call["name"], call["input"])
                    emit({"type": "tool_call_result", "name": call["name"], "result": output})
                    tool_results.append({"name": call["name"], "result": output})
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": output})
                self.messages.append({"role": "user", "content": results})

                if i == MAX_TOOL_ITERATIONS - 1:
                    # Say what was actually accomplished and how to resume.
                    # The old message named only the limit, so a turn that had
                    # just written five working files read as a failure.
                    used: dict[str, int] = {}
                    for entry in tool_results:
                        used[entry["name"]] = used.get(entry["name"], 0) + 1
                    breakdown = ", ".join(f"{name}×{count}" if count > 1 else name
                                          for name, count in sorted(used.items())) or "none"
                    note = (
                        f"Paused after {len(tool_results)} tool calls ({breakdown}) — "
                        f"this turn's limit. Everything done so far is saved to disk and the "
                        f"conversation is intact, so say \"continue\" and I'll pick up from here. "
                        f"Raise DARIUSAI_MAX_TOOL_ITERATIONS (currently {MAX_TOOL_ITERATIONS}) "
                        f"for longer runs."
                    )
                    emit({"type": "assistant_text", "text": note})
                    narration.append(note)
                    final_text = "\n\n".join(narration)
        finally:
            bus.publish({"kind": "agent_turn", "phase": "end", "route": COORDINATOR_ID})

        if self.on_turn_complete:
            try:
                self.on_turn_complete({
                    "user_text": user_text,
                    "assistant_text": final_text,
                    "tool_results": tool_results,
                })
            except Exception:
                # Turn logging/analytics must never fail the user-visible chat turn.
                pass

        return final_text
