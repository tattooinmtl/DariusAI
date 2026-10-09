"""DuckDuckGo and the VPS search gateway are merged, and either may fail."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.search.web_search import SearchResult, _gateway, web_search


def _hit(title, url):
    return SearchResult(title=title, url=url, snippet=title)


def test_results_are_interleaved_and_duplicate_urls_drop():
    duck = lambda query, n: [_hit("ddg", "https://a.example/x"), _hit("only-ddg", "https://b.example/y")]
    gate = lambda query, n: [_hit("gw", "https://a.example/x"), _hit("only-gw", "https://c.example/z")]
    got = web_search("q", max_results=8, ddg=duck, gateway=gate)
    assert [item.url for item in got] == [
        "https://a.example/x",
        "https://b.example/y",
        "https://c.example/z",
    ]


def test_a_dead_gateway_still_returns_duckduckgo():
    duck = lambda query, n: [_hit("ddg", "https://a.example/x")]

    def boom(query, n):
        raise RuntimeError("gateway down")

    got = web_search("q", ddg=duck, gateway=boom)
    assert [item.url for item in got] == ["https://a.example/x"]


def test_gateway_sends_the_key_and_reads_results(monkeypatch, tmp_path):
    key_file = tmp_path / "grok.key"
    key_file.write_text("test-key\n", encoding="utf-8")
    monkeypatch.setenv("SEARCH_GWN_KEY_FILE", str(key_file))
    monkeypatch.delenv("SEARCH_GWN_API_KEY", raising=False)
    seen = {}

    class Response:
        status_code = 200

        def json(self):
            return {"results": [{"title": "T", "url": "https://z.example", "snippet": "s"}]}

    def getter(url, params, headers, timeout):
        seen["url"] = url
        seen["params"] = params
        seen["auth"] = headers["Authorization"]
        return Response()

    got = _gateway("sqlite wal", 5, http_get=getter)
    assert seen["url"].endswith("/search")
    assert seen["params"]["q"] == "sqlite wal"
    assert seen["auth"] == "Bearer test-key"
    assert got[0].url == "https://z.example"


def test_gateway_without_a_key_returns_nothing(monkeypatch):
    monkeypatch.delenv("SEARCH_GWN_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_GWN_KEY_FILE", str(Path("Z:/no/such/key")))
    monkeypatch.setattr("dariusai.search.web_search.Path.home", lambda: Path("Z:/no-home"))
    assert _gateway("q", 5, http_get=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no request"))) == []
