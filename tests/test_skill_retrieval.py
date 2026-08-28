"""Skill distillation — the RAG lookup that replaced "load the whole
SKILL.md into the conversation".

What's pinned here:

1. Chunking: markdown splits on headings, code fences survive intact,
   frontmatter becomes an `about` passage.
2. The passage index: incremental (unchanged file = no rewrite), search
   is capped by both passage count and total characters.
3. `skill_lookup` returns passages, not bodies, and stays inside its
   character budget even against a very large skill.
4. `invoke_skill` distils a large skill by default, hands over a small
   one whole, and returns the full body on `full=True`.
5. The chat loop evicts skill payloads it has already reasoned over, and
   leaves a receipt naming the skill.
6. `persist_state` hands a compact snapshot to the sink at the end of a
   turn.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _stubs import ScriptedLLM, text_resp, tool_resp  # noqa: E402


BIG_SKILL = """---
name: giant-skill
description: A very large skill about parsing CSV files with pandas.
---

# Giant Skill

## Overview

This skill explains the overall approach to the problem at hand.

## Parsing CSV

Use `pandas.read_csv` with an explicit dtype mapping so a column of
postcodes doesn't silently become a float.

```python
import pandas as pd
df = pd.read_csv("in.csv", dtype={"postcode": "string"})
```

## Deployment checklist

Ship behind a feature flag, then watch the error rate for an hour.
"""


def _padded(text: str, chars: int) -> str:
    """The same skill, inflated past the full-load threshold with filler
    sections so the distillation path is the one under test."""
    filler = "\n\n## Appendix {i}\n\n" + ("lorem ipsum dolor sit amet " * 20)
    out = [text]
    i = 0
    while len("".join(out)) < chars:
        out.append(filler.format(i=i))
        i += 1
    return "".join(out)


@pytest.fixture
def store(tmp_path):
    from dariusai.brain.store import BrainStore
    home = tmp_path / "brain"
    home.mkdir()
    return BrainStore(home)


def _skill_project(tmp_path, name: str, body: str, group: str = "languages") -> Path:
    """A project root with one skill on disk, laid out the way invoke_skill
    globs for it."""
    root = tmp_path / "project"
    path = root / "addon" / "skills" / group / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# 1. Chunking
# ---------------------------------------------------------------------------


def test_frontmatter_becomes_an_about_passage():
    from dariusai.brain.retrieval import chunk_markdown

    chunks = chunk_markdown(BIG_SKILL)
    assert chunks[0][0] == "about"
    assert "parsing CSV files with pandas" in chunks[0][1]


def test_headings_are_passage_boundaries():
    from dariusai.brain.retrieval import chunk_markdown

    headings = [h for h, _ in chunk_markdown(BIG_SKILL)]
    assert "Giant Skill > Overview" in headings
    assert "Giant Skill > Parsing CSV" in headings
    assert "Giant Skill > Deployment checklist" in headings


def test_code_fences_are_not_split():
    """A half-open fence reads as prose to the model — worse than no code
    at all."""
    from dariusai.brain.retrieval import chunk_markdown

    for _, body in chunk_markdown(BIG_SKILL):
        assert body.count("```") % 2 == 0


def test_oversized_section_is_split_at_line_boundaries():
    from dariusai.brain.retrieval import chunk_markdown

    text = "# H\n\n" + "\n".join(f"line {i} of a very long unbroken section" for i in range(400))
    chunks = chunk_markdown(text, target_chars=400, max_chars=800)
    assert len(chunks) > 1
    assert all(len(b) <= 800 for _, b in chunks)
    # Nothing was lost or mangled mid-line.
    rejoined = "\n".join(b for _, b in chunks)
    assert "line 399 of a very long unbroken section" in rejoined


# ---------------------------------------------------------------------------
# 2. The index
# ---------------------------------------------------------------------------


def test_index_is_incremental(store, tmp_path):
    from dariusai.brain.retrieval import PassageIndex

    index = PassageIndex(store.conn)
    if not index.enabled:
        pytest.skip("SQLite built without FTS5")
    path = tmp_path / "giant-skill" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(BIG_SKILL, encoding="utf-8")

    assert index.sync_file(path) > 0
    assert index.sync_file(path) == 0, "unchanged file must not be reindexed"
    path.write_text(BIG_SKILL + "\n\n## New\n\nsomething else\n", encoding="utf-8")
    assert index.sync_file(path) > 0


def test_search_is_capped_by_count_and_characters(store, tmp_path):
    from dariusai.brain.retrieval import PassageIndex

    index = PassageIndex(store.conn)
    if not index.enabled:
        pytest.skip("SQLite built without FTS5")
    index.index_text("giant-skill", "x/SKILL.md", _padded(BIG_SKILL, 40_000))

    hits = index.search("lorem ipsum dolor", k=3, budget_chars=1200)
    assert 0 < len(hits) <= 3
    assert sum(len(h.body) for h in hits) <= 1200 + 1400  # first passage may exceed the budget alone


def test_search_finds_the_relevant_section_not_the_document(store):
    from dariusai.brain.retrieval import PassageIndex

    index = PassageIndex(store.conn)
    if not index.enabled:
        pytest.skip("SQLite built without FTS5")
    index.index_text("giant-skill", "x/SKILL.md", BIG_SKILL)

    hits = index.search("dtype mapping for postcodes in read_csv", k=2)
    assert hits
    assert "read_csv" in hits[0].body
    assert "Deployment checklist" not in hits[0].body


# ---------------------------------------------------------------------------
# 3. skill_lookup
# ---------------------------------------------------------------------------


def test_skill_lookup_returns_passages_not_bodies(store, tmp_path):
    from dariusai.agent.tools import _skill_lookup
    from dariusai.brain.retrieval import PassageIndex

    if not PassageIndex(store.conn).enabled:
        pytest.skip("SQLite built without FTS5")
    body = _padded(BIG_SKILL, 40_000)
    store.set_setting("project_dir", str(_skill_project(tmp_path, "giant-skill", body)))

    out = _skill_lookup(store, "how do I parse a csv with an explicit dtype")
    assert "read_csv" in out
    assert len(out) < 4_000, f"lookup returned {len(out)} chars — that is a body, not passages"
    assert len(out) < len(body) / 5


def test_skill_lookup_can_be_restricted_to_one_skill(store, tmp_path):
    from dariusai.agent.tools import _skill_lookup
    from dariusai.brain.retrieval import PassageIndex

    if not PassageIndex(store.conn).enabled:
        pytest.skip("SQLite built without FTS5")
    root = _skill_project(tmp_path, "giant-skill", BIG_SKILL)
    other = root / "addon" / "skills" / "languages" / "other-skill" / "SKILL.md"
    other.parent.mkdir(parents=True)
    other.write_text("---\nname: other-skill\n---\n\n# Other\n\nParsing CSV the wrong way.\n",
                     encoding="utf-8")
    store.set_setting("project_dir", str(root))

    out = _skill_lookup(store, "parsing csv", skill="other-skill")
    assert "other-skill" in out
    assert "giant-skill" not in out


def test_skill_lookup_rejects_an_empty_query(store):
    from dariusai.agent.tools import _skill_lookup
    assert _skill_lookup(store, "  ").startswith("ERROR")


# ---------------------------------------------------------------------------
# 4. invoke_skill
# ---------------------------------------------------------------------------


def test_small_skill_still_comes_back_whole(store, tmp_path):
    """Under the threshold the RAG detour costs more than it saves."""
    from dariusai.agent.tools import _invoke_skill

    store.set_setting("project_dir", str(_skill_project(tmp_path, "giant-skill", BIG_SKILL)))
    out = _invoke_skill(store, "giant-skill")
    assert out == BIG_SKILL


def test_large_skill_is_distilled_by_default(store, tmp_path):
    from dariusai.agent.tools import _invoke_skill
    from dariusai.brain.retrieval import PassageIndex

    if not PassageIndex(store.conn).enabled:
        pytest.skip("SQLite built without FTS5")
    body = _padded(BIG_SKILL, 40_000)
    store.set_setting("project_dir", str(_skill_project(tmp_path, "giant-skill", body)))

    out = _invoke_skill(store, "giant-skill", query="parse a csv with an explicit dtype")
    assert len(out) < len(body) / 5
    assert "distilled" in out
    assert "read_csv" in out, "the section the query asked for must be in there"
    assert "Sections:" in out, "the outline is what lets the model ask for another part"
    assert "full=true" in out


def test_full_true_returns_the_whole_body(store, tmp_path):
    from dariusai.agent.tools import _invoke_skill

    body = _padded(BIG_SKILL, 40_000)
    store.set_setting("project_dir", str(_skill_project(tmp_path, "giant-skill", body)))
    assert _invoke_skill(store, "giant-skill", full=True) == body


def test_unknown_skill_still_says_so(store, tmp_path):
    from dariusai.agent.tools import _invoke_skill

    store.set_setting("project_dir", str(_skill_project(tmp_path, "giant-skill", BIG_SKILL)))
    assert "no skill named" in _invoke_skill(store, "nope-not-here")


def test_skill_lookup_is_a_registered_tool(store, tmp_path):
    from dariusai.agent.tools import build_tool_registry

    reg = build_tool_registry(store)
    assert "skill_lookup" in reg.specs
    invoke = reg.specs["invoke_skill"].input_schema["properties"]
    assert "query" in invoke and "full" in invoke


# ---------------------------------------------------------------------------
# 5. Payload eviction in the chat loop
# ---------------------------------------------------------------------------


def _session(llm, tmp_path):
    from dariusai.agent.chat import ChatSession
    from dariusai.agent.tools import build_tool_registry
    from dariusai.brain.store import BrainStore

    home = tmp_path / "brain2"
    home.mkdir()
    store = BrainStore(home)
    # Padded past SKILL_PAYLOAD_EVICT_MIN_CHARS: eviction deliberately
    # ignores payloads small enough that dropping them would cost more in
    # prompt-cache invalidation than it saves in re-sent bytes.
    store.set_setting("project_dir",
                      str(_skill_project(tmp_path, "giant-skill", _padded(BIG_SKILL, 8_000))))
    return ChatSession(llm=llm, tools=build_tool_registry(store))


def test_skill_body_is_evicted_after_its_ttl(tmp_path):
    """The whole point: a body read early in a turn stops being re-sent
    once the model has acted on it."""
    llm = ScriptedLLM([
        tool_resp("c1", "invoke_skill", {"name": "giant-skill", "full": True}),
        tool_resp("c2", "list_dir", {"path": "."}),
        tool_resp("c3", "list_dir", {"path": "."}),
        tool_resp("c4", "list_dir", {"path": "."}),
        text_resp("done"),
    ])
    session = _session(llm, tmp_path)
    session.skill_payload_ttl = 1
    session.send("go")

    payloads = [
        block["content"]
        for msg in session.messages if isinstance(msg.get("content"), list)
        for block in msg["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
        and block.get("tool_use_id") == "c1"
    ]
    assert payloads, "the skill tool_result must still be in the history"
    assert "Parsing CSV" not in payloads[0], "the body should have been dropped"
    assert "giant-skill" in payloads[0], "the receipt must name the skill"
    assert "skill_lookup" in payloads[0], "the receipt must say how to get it back"


def test_recent_skill_body_is_left_alone(tmp_path):
    """Evicting a body the model hasn't reasoned over yet would break the
    very turn it was fetched for."""
    llm = ScriptedLLM([
        tool_resp("c1", "invoke_skill", {"name": "giant-skill", "full": True}),
        text_resp("done"),
    ])
    session = _session(llm, tmp_path)
    session.send("go")

    payloads = [
        block["content"]
        for msg in session.messages if isinstance(msg.get("content"), list)
        for block in msg["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert any("Parsing CSV" in p for p in payloads)


def test_eviction_emits_an_event(tmp_path):
    llm = ScriptedLLM([
        tool_resp("c1", "invoke_skill", {"name": "giant-skill", "full": True}),
        tool_resp("c2", "list_dir", {"path": "."}),
        tool_resp("c3", "list_dir", {"path": "."}),
        tool_resp("c4", "list_dir", {"path": "."}),
        text_resp("done"),
    ])
    session = _session(llm, tmp_path)
    session.skill_payload_ttl = 1
    events: list[dict] = []
    session.send("go", on_event=events.append)

    evictions = [e for e in events if e["type"] == "skill_payloads_evicted"]
    assert evictions and evictions[0]["saved_chars"] > 0
    assert "giant-skill" in evictions[0]["skills"]


# ---------------------------------------------------------------------------
# 6. Persisted state
# ---------------------------------------------------------------------------


def test_turn_persists_a_compact_state_snapshot(tmp_path):
    llm = ScriptedLLM([
        tool_resp("c1", "invoke_skill", {"name": "giant-skill", "full": True}),
        text_resp("all done"),
    ])
    session = _session(llm, tmp_path)
    saved: list[tuple[str, dict]] = []
    session.state_sink = lambda sid, snap: saved.append((sid, snap))
    session.send("go")

    assert saved, "state must be persisted at the end of a turn"
    sid, snapshot = saved[-1]
    assert sid == session.session_id
    assert "giant-skill" in snapshot["skills_used"]
    assert snapshot["last_assistant_text"] == "all done"
    # Compact by construction: names and paths, never bodies.
    assert "Parsing CSV" not in repr(snapshot)


def test_a_failing_state_sink_does_not_break_the_turn(tmp_path):
    def boom(sid, snapshot):
        raise RuntimeError("disk on fire")

    llm = ScriptedLLM([text_resp("still fine")])
    session = _session(llm, tmp_path)
    session.state_sink = boom
    assert session.send("go") == "still fine"
