"""Tests for configurable knowledge retrieval. / 可配置知识检索测试。"""

from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from zharness.config import KnowledgeHybridSettings, KnowledgeRetrievalSettings
from zharness.knowledge.retrieval import KnowledgeRetriever


class FakeLangChainRetriever:
    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents
        self.queries: list[str] = []

    async def ainvoke(self, query: str) -> list[Document]:
        self.queries.append(query)
        return self.documents


class FakeVectorStore:
    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents
        self.calls: list[dict] = []

    def as_retriever(self, **kwargs):
        self.calls.append(kwargs)
        return FakeLangChainRetriever(self.documents)


class FakeRepository:
    async def adjacent_chunks(self, thread_id, document_id, ordinal, *, radius=1):
        return []


def _document() -> Document:
    return Document(
        id="chunk-1",
        page_content="会话知识",
        metadata={
            "document_id": "doc-1",
            "ordinal": 2,
            "title": "notes.md",
            "source_uri": "/workspace/notes.md",
            "locator": {"start_line": 3},
        },
    )


@pytest.mark.asyncio
async def test_hybrid_similarity_uses_fresh_config_and_thread_filter() -> None:
    store = FakeVectorStore([_document()])
    retriever = KnowledgeRetriever(
        store,  # type: ignore[arg-type]
        FakeRepository(),  # type: ignore[arg-type]
        KnowledgeRetrievalSettings(),
    )

    first = await retriever.search("thread-a", "混合检索")
    second = await retriever.search("thread-b", "向量搜索")

    first_kwargs = store.calls[0]["search_kwargs"]
    second_kwargs = store.calls[1]["search_kwargs"]
    assert store.calls[0]["search_type"] == "similarity"
    assert first_kwargs["filter"] == {
        "$and": [{"thread_id": "thread-a"}, {"is_active": True}]
    }
    assert second_kwargs["filter"] == {
        "$and": [{"thread_id": "thread-b"}, {"is_active": True}]
    }
    assert (
        first_kwargs["hybrid_search_config"]
        is not second_kwargs["hybrid_search_config"]
    )
    assert "混合" in first_kwargs["hybrid_search_config"].fts_query
    assert first["results"][0]["chunk_id"] == "chunk-1"
    assert second["count"] == 1


@pytest.mark.asyncio
async def test_search_accepts_bound_knowledge_base_scopes() -> None:
    store = FakeVectorStore([])
    retriever = KnowledgeRetriever(
        store,  # type: ignore[arg-type]
        FakeRepository(),  # type: ignore[arg-type]
        KnowledgeRetrievalSettings(),
    )

    await retriever.search(
        "thread-a",
        "reference",
        scope_ids=["thread-a", "knowledge-base:base-a"],
    )

    assert store.calls[0]["search_kwargs"]["filter"] == {
        "$and": [
            {"thread_id": {"$in": ["thread-a", "knowledge-base:base-a"]}},
            {"is_active": True},
        ]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_type", "extra_key", "extra_value"),
    [
        ("mmr", "fetch_k", 17),
        ("similarity_score_threshold", "score_threshold", 0.7),
    ],
)
async def test_langchain_search_strategy_switching(
    search_type: str,
    extra_key: str,
    extra_value: float,
) -> None:
    store = FakeVectorStore([])
    score_threshold = 0.7 if search_type == "similarity_score_threshold" else None
    settings = KnowledgeRetrievalSettings(
        search_type=search_type,
        fetch_k=17,
        score_threshold=score_threshold,
        hybrid=KnowledgeHybridSettings(enabled=False),
    )
    retriever = KnowledgeRetriever(
        store,  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        settings,
    )

    await retriever.search("thread-a", "query")

    assert store.calls[0]["search_type"] == search_type
    assert store.calls[0]["search_kwargs"][extra_key] == extra_value
    assert "hybrid_search_config" not in store.calls[0]["search_kwargs"]


def test_hybrid_rejects_non_similarity_search_type() -> None:
    with pytest.raises(ValueError, match="requires search_type=similarity"):
        KnowledgeRetrievalSettings(search_type="mmr")
