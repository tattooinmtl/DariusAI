import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.brain.learn import InsufficientResearchError, save_learned_skill
from dariusai.brain.skill import Source
from dariusai.brain.store import BrainStore

GOOD_SOURCES = [
    Source(url="https://pkg.go.dev/time#Ticker", quote="Stop does not close the channel"),
    Source(url="https://go.dev/blog/timers", quote="always call Stop when a Ticker is no longer needed"),
    Source(url="https://stackoverflow.com/q/123", quote="leaked goroutine reproduction"),
    Source(url="https://github.com/golang/go/issues/456", quote="confirmed as expected behavior"),
    Source(url="https://example.com/blog/tickers", quote="third-party confirmation"),
]


def base_kwargs(**overrides):
    kwargs = dict(
        title="Goroutine leak from an unstopped time.Ticker",
        problem="A Ticker's goroutine runs forever unless Stop() is called.",
        solution="defer ticker.Stop() right after time.NewTicker.",
        code_examples="ticker := time.NewTicker(time.Second)\ndefer ticker.Stop()",
        best_practices="Always pair NewTicker with a deferred Stop in the same scope.",
        edge_cases="Stop() doesn't close the channel; a drainer just stops receiving, no panic.",
        sources=GOOD_SOURCES,
        tags=["go", "concurrency"],
    )
    kwargs.update(overrides)
    return kwargs


def test_save_learned_skill_with_enough_sources(tmp_path):
    store = BrainStore(tmp_path)
    skill = save_learned_skill(store, **base_kwargs())
    assert skill.id
    fetched = store.get_skill(skill.id)
    assert len(fetched.sources) == 5


def test_rejects_fewer_than_five_sources(tmp_path):
    store = BrainStore(tmp_path)
    with pytest.raises(InsufficientResearchError):
        save_learned_skill(store, **base_kwargs(sources=GOOD_SOURCES[:3]))


def test_rejects_sources_all_from_one_domain(tmp_path):
    store = BrainStore(tmp_path)
    same_domain_sources = [
        Source(url=f"https://example.com/page{i}", quote=f"quote {i}") for i in range(5)
    ]
    with pytest.raises(InsufficientResearchError):
        save_learned_skill(store, **base_kwargs(sources=same_domain_sources))


def test_rejects_source_with_empty_quote(tmp_path):
    store = BrainStore(tmp_path)
    sources = GOOD_SOURCES[:-1] + [Source(url="https://example.org/x", quote="")]
    with pytest.raises(InsufficientResearchError):
        save_learned_skill(store, **base_kwargs(sources=sources))


def test_nothing_written_on_rejection(tmp_path):
    store = BrainStore(tmp_path)
    try:
        save_learned_skill(store, **base_kwargs(sources=GOOD_SOURCES[:2]))
    except InsufficientResearchError:
        pass
    payload = store.to_graph_payload()
    assert payload["counts"]["nodes"] == 1  # just the coordinator, nothing filed
