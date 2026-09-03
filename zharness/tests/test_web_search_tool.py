from ddgs.exceptions import RatelimitException
from zharness.tools import web_search as web_search_module
from zharness.tools.constants import NETWORK_REQUEST_TIMEOUT_SECONDS
from zharness.tools.web_search import web_search

_RAW_RESULTS = [
    {
        "title": "Example Docs",
        "href": "https://example.com/docs",
        "body": "Docs are the best place to start.",
    },
    {
        "title": "Plain Link Result",
        "href": "https://plain.example.com/page",
        "body": "",
    },
    {"title": "", "href": "https://skip.example.com", "body": "no title"},
]


class _FakeClient:
    def __init__(self, results) -> None:
        self._results = results

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def text(self, query: str, max_results: int | None = None) -> list[dict]:
        _ = query
        return self._results if max_results is None else self._results[:max_results]


def _patch_client(monkeypatch, results) -> None:
    monkeypatch.setattr(
        web_search_module,
        "DDGS",
        lambda *args, **kwargs: _FakeClient(results),
    )


def test_runtime_is_hidden_from_web_search_schema() -> None:
    assert set(web_search.args) == {"query", "max_results"}


def test_web_search_parses_results(monkeypatch) -> None:
    _patch_client(monkeypatch, _RAW_RESULTS)

    results = web_search.func("example docs")

    assert results == [
        {
            "title": "Example Docs",
            "url": "https://example.com/docs",
            "snippet": "Docs are the best place to start.",
        },
        {"title": "Plain Link Result", "url": "https://plain.example.com/page"},
    ]


def test_web_search_uses_shared_network_timeout(monkeypatch) -> None:
    options: dict[str, int] = {}

    def client_factory(*args, **kwargs):
        _ = args
        options.update(kwargs)
        return _FakeClient([])

    monkeypatch.setattr(web_search_module, "DDGS", client_factory)

    web_search.func("example docs")

    assert options == {"timeout": NETWORK_REQUEST_TIMEOUT_SECONDS}


def test_web_search_respects_max_results(monkeypatch) -> None:
    _patch_client(monkeypatch, _RAW_RESULTS)

    results = web_search.func("example docs", max_results=1)

    assert results == [
        {
            "title": "Example Docs",
            "url": "https://example.com/docs",
            "snippet": "Docs are the best place to start.",
        }
    ]


def test_web_search_returns_empty_message_when_no_results(monkeypatch) -> None:
    _patch_client(monkeypatch, [])

    result = web_search.func("nothing matches")

    assert result == "No results found. Try a different, more specific query."


def test_web_search_surfaces_rate_limit_errors(monkeypatch) -> None:
    def fail(*args, **kwargs):
        _ = args, kwargs
        raise RatelimitException("rate limit exceeded")

    monkeypatch.setattr(web_search_module, "DDGS", fail)

    result = web_search.func("example")

    assert isinstance(result, str)
    assert result.startswith("Error: DuckDuckGo request failed:")


def test_web_search_validates_inputs() -> None:
    assert web_search.func("") == "Error: query must not be empty"
    assert web_search.func("   ") == "Error: query must not be empty"
    assert web_search.func("x" * 513) == ("Error: query must be at most 512 characters")
    assert web_search.func("ok", max_results=0) == (
        "Error: max_results must be between 1 and 10"
    )
    assert web_search.func("ok", max_results=11) == (
        "Error: max_results must be between 1 and 10"
    )
    assert web_search.func("ok", max_results=True) == (
        "Error: max_results must be between 1 and 10"
    )
