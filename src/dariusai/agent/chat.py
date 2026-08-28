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
import time
import uuid
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

# Tools whose results are reference material: read once, acted on, then
# dead weight in every subsequent prompt of the turn. They are evicted on
# a timer (see `skill_payload_ttl`) rather than waiting for compaction,
# which only fires once the window is already 75% full.
SKILL_TOOLS = {"invoke_skill", "load_skill", "skill_lookup"}
SKILL_PAYLOAD_TTL = int(os.environ.get("DARIUSAI_SKILL_PAYLOAD_TTL", "2"))
# Bodies at or under this stay put — evicting them saves nothing and
# costs the model the content.
SKILL_PAYLOAD_MIN_CHARS = 800
# And an eviction pass only runs once the stale payloads add up to this
# much, because rewriting history invalidates the prompt cache from that
# point onwards. Roughly a thousand tokens: worth one cache re-write.
SKILL_PAYLOAD_EVICT_MIN_CHARS = int(os.environ.get("DARIUSAI_SKILL_EVICT_MIN_CHARS", "4000"))

CHAT_SYSTEM = with_doctrine(
    "You are DariusAI, a self-improving polyglot coding agent. Use the tools available to read/write "
    "files and run shell commands in any language's toolchain. "
    "When returning code to the user, always output complete, fully formed files/pages in code blocks so they can be sent directly to the editor as working files. "
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
    # Per-call token usage. The chat panel renders the "X / Y (k% left)"
    # gauge from `current_input_tokens` against the model's context
    # window — that is the actual size of the prompt being sent to
    # the model right now, which is what the user wants to monitor.
    # Summing across all calls (the previous "cumulative" approach)
    # confuses the gauge: a long conversation kept pushing the number
    # past the window even though any single call was well within it.
    # The running total of all tokens processed is exposed as
    # `total_tokens_processed` so the user still has a session-level
    # view; the chat-panel gauge just doesn't use it.
    current_input_tokens: int = 0
    current_output_tokens: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    context_window: int = 0
    # Prompt-cache accounting. `current_input_tokens` above is the *whole*
    # prompt (cached + uncached); these say how much of it was served from
    # the cache at ~0.1x. A hit rate stuck at zero is the tell for a silent
    # cache invalidator, which is otherwise invisible until the bill.
    current_cache_read_tokens: int = 0
    current_cache_write_tokens: int = 0
    total_cache_read_tokens: int = 0
    # Auto-compaction controls. The session calls `compact(force=False)`
    # at the top of each LLM iteration; the call is a no-op unless the
    # prompt is already past `compact_threshold_ratio * context_window`,
    # or `force=True` is passed (the UI's [⚡ Compact] button). The
    # compacted messages shape is two stub turns (summary + acknowledgement)
    # followed by the last `keep_recent_turns` messages — recent file
    # paths, active tool calls, and the question under discussion are
    # never lost.
    auto_compact_enabled: bool = True
    compact_threshold_ratio: float = 0.75
    keep_recent_turns: int = 6
    # Truncation ceiling for tool_result strings inside the older turns
    # during compaction. Anything longer is replaced with a short stub
    # that preserves the "this output existed" signal but not the bytes.
    tool_output_truncate_chars: int = 500
    # Skill payload eviction. A skill body read at iteration 3 of a
    # 60-iteration turn is otherwise re-sent to the provider 57 more
    # times, long after the model has acted on it. `skill_payload_ttl`
    # is how many further iterations a body stays verbatim before it
    # is replaced with a one-line receipt naming the skill and how to
    # get it back. The model keeps the *fact* that it read the skill —
    # what it loses is the bytes it already used.
    skill_payload_ttl: int = SKILL_PAYLOAD_TTL
    # Minimum total stale payload before an eviction pass runs at all —
    # see `_evict_skill_payloads` for why this interacts with the cache.
    skill_payload_evict_min_chars: int = SKILL_PAYLOAD_EVICT_MIN_CHARS
    # Session state that survives compaction: what the turn touched,
    # not what was said about it. Persisted through `state_sink` at the
    # end of every turn (the websocket wires it to the brain's settings
    # table) so a reconnecting client can rehydrate without replaying
    # the conversation.
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: dict[str, Any] = field(default_factory=dict)
    state_sink: Callable[[str, dict[str, Any]], None] | None = None
    # tool_use_id -> {name, iteration, chars} for skill bodies currently
    # sitting verbatim in `messages`.
    _skill_payloads: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def send(self, user_text: str, on_event: EventCallback | None = None) -> str:
        """Append a user message, run the tool-calling loop until the model
        replies with no further tool calls (or the iteration cap), and
        return the final assistant text. `on_event` is called for every
        intermediate step (assistant text, tool call start/result) so a
        caller can stream progress instead of waiting for the whole turn."""
        def emit(event: dict[str, Any]) -> None:
            if on_event:
                on_event(event)

        # Wire the per-call event publisher into the tool registry. Tools
        # like `set_todos` call this to drive the chat panel's phases
        # panel. The publisher is set fresh per call so the chat session
        # survives multiple turns under the same WebSocket (each turn
        # passes its own on_event).
        self.tools.on_event = on_event if on_event else None

        # Fresh turn = fresh permission slate. External-read grants
        # approved during the previous turn are dropped so the "one
        # grant per prompt" contract holds — if the agent needs the
        # same folder again it has to ask the human again.
        sandbox = getattr(self.tools, "sandbox", None)
        if sandbox is not None and hasattr(sandbox, "clear_external_grants"):
            sandbox.clear_external_grants()

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
                # Drop skill bodies the model has already reasoned over.
                # Runs before the compaction check because it is the
                # cheaper lever: no LLM call, no history rewrite, and it
                # keeps the prompt under the compaction threshold for
                # longer.
                evicted = self._evict_skill_payloads(i)
                if evicted:
                    emit({"type": "skill_payloads_evicted", **evicted})

                # Auto-compact before the next LLM call when the prior
                # call's prompt is already past the threshold. A no-op
                # on the first iteration (no prior usage yet) and any
                # time we're below the threshold — the only cost is one
                # cheap arithmetic check per iteration.
                if self.auto_compact_enabled and self.context_window:
                    result = self.compact(force=False)
                    if result.get("status") == "ok":
                        emit({"type": "context_compacted", **result})

                # Time the LLM call so the chat panel can show tokens/sec.
                # perf_counter is monotonic; we run it across the full
                # round-trip (HTTP + SDK parsing) so the TPS reflects
                # real throughput, not just the model's decode time.
                t0 = time.perf_counter()
                resp = self.llm.complete(system=self.system, messages=self.messages, tools=tool_schemas)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self.messages.append({"role": "assistant", "content": resp["content"]})

                # Capture per-call usage. The LLM impl fills `usage`
                # and `context_window` in production; the test stub
                # omits them, so we tolerate their absence.
                usage = resp.get("usage") or {}
                # With prompt caching on, `input_tokens` is only the
                # *uncached* remainder — the cached prefix is reported
                # separately. The gauge and the auto-compaction threshold
                # both want the real prompt size, so the three add up;
                # reading input_tokens alone would show a 60-iteration
                # turn as using almost no context and compaction would
                # never fire.
                cache_read = int(usage.get("cache_read_input_tokens") or 0)
                cache_write = int(usage.get("cache_creation_input_tokens") or 0)
                in_tok = int(usage.get("input_tokens") or 0) + cache_read + cache_write
                out_tok = int(usage.get("output_tokens") or 0)
                self.current_cache_read_tokens = cache_read
                self.current_cache_write_tokens = cache_write
                self.total_cache_read_tokens += cache_read
                # Replace the cumulative semantics with "current call":
                # the value the chat panel renders is the size of *this*
                # prompt, not the sum across the whole session. The
                # totals are still tracked for the session-level view
                # if the user surfaces them later (e.g. via a tool).
                self.current_input_tokens = in_tok
                self.current_output_tokens = out_tok
                self.total_input_tokens += in_tok
                self.total_output_tokens += out_tok
                if not self.context_window:
                    self.context_window = int(resp.get("context_window") or 0)
                self._emit_token_stats(emit, in_tok, out_tok, elapsed_ms)

                text = _text_of(resp["content"])
                tool_uses = [b for b in resp["content"] if b.get("type") == "tool_use"]
                if not tool_uses:
                    # Final answer from the model (or empty content).
                    # Emit with is_final=True so the chat panel closes
                    # the thinking box, even if the model returned no
                    # text — a model that produces empty content blocks
                    # should still end the turn visibly, not leave the
                    # user staring at a frozen "thinking…" panel.
                    emit({"type": "assistant_text",
                          "text": text or "(no response)",
                          "is_final": True})
                    final_text = text
                    break
                if text:
                    narration.append(text)
                    # Intermediate narration — not the final answer yet.
                    emit({"type": "assistant_text", "text": text,
                          "is_final": False})

                results = []
                for call in tool_uses:
                    emit({"type": "tool_call_start", "name": call["name"], "input": call["input"]})
                    output = self.tools.call(call["name"], call["input"])
                    emit({"type": "tool_call_result", "name": call["name"], "result": output})
                    tool_results.append({"name": call["name"], "result": output})
                    results.append({"type": "tool_result", "tool_use_id": call["id"], "content": output})
                    # Observe: record what the call did in session state,
                    # and mark reference payloads for eviction later in
                    # the turn.
                    self._observe(i, call, output)
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
                    emit({"type": "assistant_text", "text": note, "is_final": True})
                    narration.append(note)
                    final_text = "\n\n".join(narration)
        finally:
            bus.publish({"kind": "agent_turn", "phase": "end", "route": COORDINATOR_ID})

        # Persist state before responding: the compact record of what the
        # turn touched outlives both the eviction above and any later
        # compaction, so a reconnect (or the next turn) starts from facts
        # rather than from a re-read of everything.
        self.persist_state(user_text=user_text, assistant_text=final_text)

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

    # ------------------------------------------------------------------
    # Observe / evict / persist — the cheap half of the loop
    # ------------------------------------------------------------------

    def _observe(self, iteration: int, call: dict[str, Any], output: str) -> None:
        """Fold one tool result into session state, and register skill
        bodies for later eviction.

        State is deliberately tiny — names and paths, never bodies. It is
        the thing that has to survive when the bodies don't.
        """
        name = call.get("name", "")
        args = call.get("input") or {}

        if name in SKILL_TOOLS and len(output) >= SKILL_PAYLOAD_MIN_CHARS:
            label = str(args.get("name") or args.get("skill_id") or args.get("query") or name)
            self._skill_payloads[call["id"]] = {
                "name": name, "label": label, "iteration": iteration, "chars": len(output),
            }
            used = self.state.setdefault("skills_used", [])
            if label not in used:
                used.append(label)
        elif name in ("write_file", "read_file") and args.get("path"):
            key = "files_written" if name == "write_file" else "files_read"
            paths = self.state.setdefault(key, [])
            if args["path"] not in paths:
                paths.append(args["path"])
        elif name == "set_todos":
            self.state["todos"] = args.get("items") or []

    def _evict_skill_payloads(self, iteration: int) -> dict[str, Any]:
        """Replace skill bodies older than `skill_payload_ttl` iterations
        with a one-line receipt.

        The receipt names the skill and says how to get it back, so the
        model can re-fetch the one section it still needs instead of
        carrying the whole document for the rest of the turn. Returns a
        summary dict (empty when nothing was evicted) for the UI.
        """
        if self.skill_payload_ttl < 0 or not self._skill_payloads:
            return {}
        stale = {
            tid: meta for tid, meta in self._skill_payloads.items()
            if iteration - meta["iteration"] > self.skill_payload_ttl
        }
        if not stale:
            return {}
        # Rewriting history invalidates the prompt cache from that point,
        # so eviction has to be worth the re-write it forces: hold the
        # stale payloads until they add up to something, then drop them
        # in one batch. Trickling out 900 chars at a time would cost more
        # in cache misses than it saves in re-sent bytes.
        if sum(m["chars"] for m in stale.values()) < self.skill_payload_evict_min_chars:
            return {}

        saved = 0
        for message in self.messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                meta = stale.get(block.get("tool_use_id"))
                if not meta or not isinstance(block.get("content"), str):
                    continue
                saved += len(block["content"])
                block["content"] = (
                    f"[{meta['name']}({meta['label']}) — {meta['chars']:,} chars already read and acted on; "
                    f"body dropped to save context. Call skill_lookup(query=...) or "
                    f"invoke_skill(name='{meta['label']}', query=...) if you need a specific part again.]"
                )
        for tid in stale:
            self._skill_payloads.pop(tid, None)
        return {"count": len(stale), "saved_chars": saved,
                "skills": [m["label"] for m in stale.values()]}

    def state_snapshot(self) -> dict[str, Any]:
        """The durable, compact view of the session — what was touched,
        not what was said."""
        return {
            "session_id": self.session_id,
            "updated_at": time.time(),
            "skills_used": self.state.get("skills_used", []),
            "files_written": self.state.get("files_written", []),
            "files_read": self.state.get("files_read", []),
            "todos": self.state.get("todos", []),
            "last_user_text": self.state.get("last_user_text", ""),
            "last_assistant_text": self.state.get("last_assistant_text", ""),
            "tokens": {
                "current_input": self.current_input_tokens,
                "total_input": self.total_input_tokens,
                "total_output": self.total_output_tokens,
                "context_window": self.context_window,
            },
        }

    def persist_state(self, user_text: str = "", assistant_text: str = "") -> dict[str, Any]:
        """Hand the snapshot to whatever the host wired up as the sink.

        No sink is a perfectly good configuration (the CLI has none), and a
        sink that raises must not take the turn down with it — state is a
        convenience, the reply is the product.
        """
        if user_text:
            self.state["last_user_text"] = user_text[:500]
        if assistant_text:
            self.state["last_assistant_text"] = assistant_text[:500]
        snapshot = self.state_snapshot()
        if self.state_sink is not None:
            try:
                self.state_sink(self.session_id, snapshot)
            except Exception:
                pass
        return snapshot

    def _emit_token_stats(self, emit, in_tok: int, out_tok: int, elapsed_ms: float) -> None:
        """Publish a `token_stats` event the chat panel renders as
        `TPS: 12.4 · 4,567 / 200,000 tok (97.7% left)`. We compute
        tokens/sec from this call's output vs the elapsed wall-clock
        time, not the cumulative — TPS is a rate, not a stock.

        The chat panel uses `current_total` (= `input_tokens` of THIS
        call) as the numerator for the "% left" gauge; the cumulative
        sum of every call across the session doesn't reflect what
        the model is actually carrying in its context window right
        now. We still emit `total_processed` so the gauge can show
        a session-level total if the user wants it, but it's not
        what the page renders by default."""
        # Tokens per second from this call's output. Floor the divisor at
        # 1ms so a provider that returns in <1ms doesn't blow up to ∞;
        # the next call will give a saner number.
        tps = out_tok / max(elapsed_ms / 1000.0, 0.001)
        current_total = in_tok + out_tok
        total_processed = self.total_input_tokens + self.total_output_tokens
        emit({
            "type": "token_stats",
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "current_total": current_total,
            "total_processed": total_processed,
            "context_window": self.context_window,
            "tps": tps,
            "elapsed_ms": elapsed_ms,
            "cache_read_tokens": self.current_cache_read_tokens,
            "cache_write_tokens": self.current_cache_write_tokens,
            # Share of this prompt that was served from cache at ~0.1x.
            "cache_hit_ratio": (self.current_cache_read_tokens / in_tok) if in_tok else 0.0,
        })

    # ------------------------------------------------------------------
    # Auto-compaction
    # ------------------------------------------------------------------

    def compact(self, force: bool = False) -> dict[str, Any]:
        """Collapse the conversation history to free up prompt space.

        Returns a dict describing what happened:

        * ``status="ok"`` — history was replaced; ``old_tokens``,
          ``new_tokens`` and ``saved_tokens`` are populated.
        * ``status="disabled"`` — ``auto_compact_enabled=False`` and
          ``force=False``; nothing was done.
        * ``status="no_window"`` — no context window seen yet; the
          threshold can't be computed.
        * ``status="too_short"`` — there aren't enough messages to
          make compaction meaningful (caller's say is the *whole*
          history here).
        * ``status="below_threshold"`` — auto-mode skipped because the
          prompt is under ``compact_threshold_ratio * context_window``.
        """
        if not self.auto_compact_enabled and not force:
            return {"status": "disabled"}
        if not self.context_window:
            return {"status": "no_window"}
        # Need at least keep_recent_turns + 2 to keep something from
        # the older half and replace the rest with the summary pair.
        if len(self.messages) <= self.keep_recent_turns + 2:
            return {"status": "too_short"}
        if not force and self.current_input_tokens < self.context_window * self.compact_threshold_ratio:
            return {"status": "below_threshold"}

        old_tokens = self.current_input_tokens
        self._compact_history()
        # The next LLM call will repopulate current_input_tokens. Until
        # then we report the count as "what we saved" being the prompt
        # size minus the keep_recent_turns messages; the chat panel
        # will re-render the gauge on the next token_stats event.
        new_tokens = self.current_input_tokens
        return {
            "status": "ok",
            "old_tokens": old_tokens,
            "new_tokens": new_tokens,
            "saved_tokens": max(old_tokens - new_tokens, 0),
        }

    def _compact_history(self) -> None:
        """Truncate oversized tool outputs in older turns, then replace
        those older turns with a single summary + acknowledgement pair.

        Tool-output truncation runs even if the summarization call
        fails — losing the noise is the whole point of the first
        step, and a chat session that crashes because the
        summarizer tripped is worse than one with a "summary
        unavailable" note.
        """
        cutoff = len(self.messages) - self.keep_recent_turns
        older = self.messages[:cutoff]
        recent = self.messages[cutoff:]

        # 1. Tool-output truncation in older turns. Only string-form
        #    tool_results are shaped `content: "..."`; structured
        #    content (image blocks, etc.) is left alone.
        self._truncate_tool_outputs(older)

        # 2. LLM summarization. Falls back to a deterministic stub if
        #    the call raises — the chat session must never be killed
        #    by a summarizer bug.
        summary = self._summarize_older(older)

        # 3. Reconstruct history. The summary is a user turn so the
        #    model sees it in the next call; the acknowledgement is
        #    an assistant turn so the alternating role sequence stays
        #    valid (Anthropic requires the first turn to be user,
        #    and OpenAI requires roles to alternate).
        self.messages = [
            {"role": "user", "content": f"[Prior Context Summary]:\n{summary}"},
            {"role": "assistant", "content": "Understood. Context compacted. Proceeding with task."},
            *recent,
        ]

    def _truncate_tool_outputs(self, messages: list[dict[str, Any]]) -> None:
        """Replace tool_result strings longer than `tool_output_truncate_chars`
        with a short stub that preserves the existence of the output but
        not the bytes. Mutates the messages in place; safe because the
        caller hands us a slice of `self.messages` that no other code
        holds a reference to."""
        cap = self.tool_output_truncate_chars
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                payload = block.get("content")
                if isinstance(payload, str) and len(payload) > cap:
                    omitted = len(payload) - cap
                    block["content"] = (
                        f"[Output truncated for context compaction: {omitted} chars omitted]"
                    )

    def _summarize_older(self, older: list[dict[str, Any]]) -> str:
        """Send the older turns to `self.llm` for a concise summary.

        The summarization prompt asks the model to preserve the four
        things a downstream user message would actually need: the
        active task, files created/edited, important decisions, and
        the current state. If the LLM raises (network, rate limit,
        bad key, exception in the SDK), we fall back to a tiny
        deterministic note — the conversation continues, the chat
        panel still gets a `context_compacted` event, and the user
        sees the failure only as a slightly less rich summary.
        """
        prompt = (
            "Summarize the prior conversation history concisely. Preserve: "
            "1) Active task/goal, 2) Files created/edited, 3) Important decisions, "
            "4) Current state."
        )
        try:
            resp = self.llm.complete(
                system=self.system,
                messages=[{"role": "user", "content": prompt}, *older],
                tools=[],
            )
            text = _text_of(resp.get("content") or [])
            if text:
                return text
        except Exception:
            pass
        # The chat session must survive a summarizer failure; the
        # tool-output truncation above already shaved the prompt.
        turns = sum(1 for m in older if m.get("role") == "user")
        return (
            f"(context compacted: {turns} earlier user turn(s); "
            "summarizer unavailable, full details dropped)"
        )
