import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.brain.skill import Skill, Source
from dariusai.brain.store import BrainStore, COORDINATOR_ID


def make_skill(**overrides) -> Skill:
    defaults = dict(
        id="",
        title="Goroutine leak from an unstopped time.Ticker",
        category="skill",
        tags=["go", "concurrency"],
        problem="A time.Ticker started with time.NewTicker keeps its goroutine alive forever unless Stop() is called.",
        solution="Always defer ticker.Stop() right after time.NewTicker, even if the ticker is used until process exit.",
        code_examples="ticker := time.NewTicker(time.Second)\ndefer ticker.Stop()",
        best_practices="Pair NewTicker/NewTimer with a defer Stop() in the same scope that created it.",
        edge_cases="Stop() does not close the channel, so a goroutine still draining ticker.C after Stop() will simply stop receiving, not panic.",
        sources=[
            Source(url="https://pkg.go.dev/time#Ticker", quote="Stop does not close the channel"),
            Source(url="https://go.dev/blog/timers", quote="always call Stop when a Ticker is no longer needed"),
            Source(url="https://example.com/a", quote="leak reproduction"),
            Source(url="https://example.com/b", quote="second source confirming"),
            Source(url="https://example.com/c", quote="third source confirming"),
        ],
        related=[],
    )
    defaults.update(overrides)
    return Skill(**defaults)


def test_skill_markdown_round_trips(tmp_path):
    skill = make_skill(id="go-ticker-leak-abc123")
    raw = skill.to_markdown()
    restored = Skill.from_markdown(raw)
    assert restored.id == skill.id
    assert restored.title == skill.title
    assert restored.problem == skill.problem
    assert restored.solution == skill.solution
    assert restored.code_examples == skill.code_examples
    assert restored.best_practices == skill.best_practices
    assert restored.edge_cases == skill.edge_cases
    assert len(restored.sources) == 5
    assert restored.sources[0].url == "https://pkg.go.dev/time#Ticker"
    assert restored.tags == ["go", "concurrency"]


def test_add_skill_writes_file_and_indexes(tmp_path):
    store = BrainStore(tmp_path)
    skill = make_skill()
    saved = store.add_skill(skill)

    assert saved.id  # auto-generated
    file_path = tmp_path / "skills" / "skill" / f"{saved.id}.md"
    assert file_path.exists()

    payload = store.to_graph_payload()
    ids = {n["id"] for n in payload["nodes"]}
    assert COORDINATOR_ID in ids
    assert saved.id in ids

    # coordinator fans out to every indexed node
    assert any(e["source"] == COORDINATOR_ID and e["target"] == saved.id for e in payload["edges"])


def test_get_skill_reads_back_full_content(tmp_path):
    store = BrainStore(tmp_path)
    saved = store.add_skill(make_skill())
    fetched = store.get_skill(saved.id)
    assert fetched.title == saved.title
    assert fetched.problem == saved.problem
    assert len(fetched.sources) == 5


def test_edit_overwrites_in_place_same_id(tmp_path):
    store = BrainStore(tmp_path)
    saved = store.add_skill(make_skill())
    saved.solution = "Updated solution text after a manual edit."
    store.add_skill(saved)

    fetched = store.get_skill(saved.id)
    assert fetched.solution == "Updated solution text after a manual edit."
    # still exactly one node for this id, not a duplicate
    payload = store.to_graph_payload()
    assert sum(1 for n in payload["nodes"] if n["id"] == saved.id) == 1


def test_related_skills_create_edges(tmp_path):
    store = BrainStore(tmp_path)
    a = store.add_skill(make_skill(id="skill-a", title="A"))
    b = store.add_skill(make_skill(id="skill-b", title="B", related=["skill-a"]))
    payload = store.to_graph_payload()
    assert any(e["source"] == "skill-b" and e["target"] == "skill-a" for e in payload["edges"])


def test_search_finds_by_tag_and_title(tmp_path):
    store = BrainStore(tmp_path)
    store.add_skill(make_skill(id="skill-go", title="Go ticker leak", tags=["go"]))
    # The body has to be overridden too, not just the title and tags: search
    # reads the description and the body, so a "Python" skill still carrying the
    # fixture's default Go text really is a match for "go".
    store.add_skill(make_skill(
        id="skill-py", title="Python asyncio pitfall", tags=["python"],
        problem="An asyncio task created with create_task is garbage collected if nothing holds a reference.",
        solution="Keep a strong reference to every task you create until it completes.",
        code_examples="task = asyncio.create_task(coro())",
        best_practices="Store tasks in a set and discard them in a done callback.",
        edge_cases="A task awaited immediately is safe; a fire-and-forget one is not.",
    ))

    hits = store.search("go")
    ids = {h["id"] for h in hits}
    assert "skill-go" in ids
    assert "skill-py" not in ids


def test_search_reaches_the_body_not_just_the_title(tmp_path):
    """The defect this replaced: a skill whose subject appears only in its body
    was unreachable, so the brain held answers it could not surface."""
    store = BrainStore(tmp_path)
    store.add_skill(make_skill(
        id="skill-conn", title="Ticker hygiene", tags=["cleanup"],
        problem="Connection pools exhaust under load when handles are never returned.",
        solution="Always release the connection in a finally block.",
    ))
    ids = {h["id"] for h in store.search("connection pools")}
    assert "skill-conn" in ids, "search must match the description, not only the label"


def test_search_never_returns_skill_bodies(tmp_path):
    """Search-then-load: the shortlist stays cheap, bodies cost a load_skill."""
    store = BrainStore(tmp_path)
    store.add_skill(make_skill(id="skill-go", title="Go ticker leak", tags=["go"]))
    for hit in store.search("go"):
        assert set(hit) == {"id", "category", "label", "tags", "usage_count"}


def test_search_survives_punctuation_and_operators(tmp_path):
    """An agent types `three.js` or `C++`; FTS syntax must not leak through."""
    store = BrainStore(tmp_path)
    store.add_skill(make_skill(id="skill-go", title="Go ticker leak", tags=["go"]))
    for query in ["three.js", "C++", "async/await", '"', "AND", "foo*bar", "", "   "]:
        store.search(query)  # must not raise


def test_delete_removes_file_and_node(tmp_path):
    store = BrainStore(tmp_path)
    saved = store.add_skill(make_skill())
    file_path = tmp_path / saved.id  # placeholder, real path below
    store.delete(saved.id)
    payload = store.to_graph_payload()
    assert all(n["id"] != saved.id for n in payload["nodes"])


def test_usage_count_increments(tmp_path):
    store = BrainStore(tmp_path)
    saved = store.add_skill(make_skill())
    store.touch_usage(saved.id)
    store.touch_usage(saved.id)
    payload = store.to_graph_payload()
    node = next(n for n in payload["nodes"] if n["id"] == saved.id)
    assert node["usage_count"] == 2


def test_nodes_whose_file_vanished_stay_searchable(tmp_path):
    """Older brains carry index rows whose markdown file is gone. Building the
    full-text index must not quietly drop them from search — they were findable
    by label and tags before it existed, and must not get *less* findable."""
    store = BrainStore(tmp_path)
    saved = store.add_skill(make_skill(id="skill-go", title="Go ticker leak", tags=["go"]))

    # simulate the orphan: index row kept, file removed
    (tmp_path / store.conn.execute(
        "SELECT file_path FROM nodes WHERE id = ?", (saved.id,)
    ).fetchone()[0]).unlink()

    store._reindex_all()
    indexed = store.conn.execute("SELECT COUNT(*) FROM nodes_fts").fetchone()[0]
    total = store.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    assert indexed == total, "every node must be indexed, file present or not"
    assert "skill-go" in {h["id"] for h in store.search("ticker")}
