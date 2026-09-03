"""Agent tool for DuckDuckGo web search. / DuckDuckGo 网页搜索的 Agent 工具。

DuckDuckGo is free and requires no API key, so the tool works out of the box.
The ``ddgs`` library impersonates a real browser to pass DuckDuckGo's anti-bot
challenges, which otherwise serve an anomaly page (HTTP 202) to flagged or
datacenter IPs instead of results.

DuckDuckGo 免费且无需 API key，因此该工具开箱即用。``ddgs`` 库模拟真实浏览器以通过
DuckDuckGo 的反爬虫挑战——否则被标记或数据中心 IP 收到的会是异常页（HTTP 202）而不是结果。
"""

from __future__ import annotations

from typing import NotRequired

from ddgs import DDGS
from ddgs.exceptions import DDGSException
from langchain.tools import tool
from typing_extensions import TypedDict

from zharness.tools.constants import NETWORK_REQUEST_TIMEOUT_SECONDS

MAX_QUERY_CHARS = 512
MAX_RESULTS = 10
DEFAULT_MAX_RESULTS = 5


class WebSearchResult(TypedDict):
    """A single web search result. / 单个网页搜索结果。"""

    title: str
    """Result title. / 结果标题。"""

    url: str
    """Absolute URL of the result. / 结果的绝对 URL。"""

    snippet: NotRequired[str]
    """Short text excerpt under the title, when available. / 标题下的简短文本摘要（如有）。"""


def _search_duckduckgo(query: str, max_results: int) -> list[WebSearchResult]:
    """Query DuckDuckGo and return the top results. / 查询 DuckDuckGo 并返回顶部结果。"""
    with DDGS(timeout=NETWORK_REQUEST_TIMEOUT_SECONDS) as client:
        raw = client.text(query, max_results=max_results)

    results: list[WebSearchResult] = []
    for item in raw[:max_results]:
        title = (item.get("title") or "").strip()
        url = (item.get("href") or "").strip()
        if not title or not url:
            continue
        result: WebSearchResult = {"title": title, "url": url}
        snippet = (item.get("body") or "").strip()
        if snippet:
            result["snippet"] = snippet
        results.append(result)
    return results


@tool
def web_search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> list[WebSearchResult] | str:
    """Search the web with DuckDuckGo and return top results with titles, URLs, and snippets.

    Use this for current information, facts, or anything beyond your training
    knowledge. DuckDuckGo is free and requires no API key but is rate-limited.
    Pass a specific, keyword-focused query.

    使用 DuckDuckGo 搜索网页，返回带标题、URL 和摘要的顶部结果。用于获取最新信息、
    事实或超出训练知识的内容。DuckDuckGo 免费且无需 API key，但受速率限制。
    请传入具体、聚焦关键词的查询。
    """
    query = query.strip()
    if not query:
        return "Error: query must not be empty"
    if len(query) > MAX_QUERY_CHARS:
        return f"Error: query must be at most {MAX_QUERY_CHARS} characters"
    if isinstance(max_results, bool) or not 1 <= max_results <= MAX_RESULTS:
        return f"Error: max_results must be between 1 and {MAX_RESULTS}"

    try:
        results = _search_duckduckgo(query, max_results)
    except DDGSException as exc:
        return f"Error: DuckDuckGo request failed: {exc}"

    if not results:
        return "No results found. Try a different, more specific query."
    return results
