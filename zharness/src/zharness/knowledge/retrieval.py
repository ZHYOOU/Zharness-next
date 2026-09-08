"""LangChain-compatible retrieval strategies for session knowledge. / 会话知识的 LangChain 兼容检索策略。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from langchain_core.documents import Document
from langchain_postgres import PGVectorStore
from langchain_postgres.v2.hybrid_search_config import (
    HybridSearchConfig,
    reciprocal_rank_fusion,
    weighted_sum_ranking,
)

from zharness.config import KnowledgeRetrievalSettings
from zharness.knowledge.lexical import lexicalize
from zharness.knowledge.repository import KnowledgeRepository
from zharness.knowledge.types import KnowledgeSearchResult


class KnowledgeRetriever:
    """Search one thread using LangChain retriever strategy semantics.

    使用 LangChain retriever 策略语义检索单个线程。
    """

    def __init__(
        self,
        vector_store: PGVectorStore,
        repository: KnowledgeRepository,
        settings: KnowledgeRetrievalSettings,
    ) -> None:
        """Initialize the retriever dependencies. / 初始化检索器依赖。"""
        self._vector_store = vector_store
        self._repository = repository
        self._settings = settings

    async def search(
        self,
        thread_id: str,
        query: str,
        *,
        limit: int | None = None,
        scope_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Retrieve ranked chunks constrained to the owning thread.

        检索严格限制在所属线程内的已排序切片。
        """
        query = query.strip()
        if not query:
            return self._response([], limit=0)

        result_limit = max(1, min(limit or self._settings.result_limit, 20))
        scopes = scope_ids or [thread_id]
        search_kwargs = self._search_kwargs(scopes, result_limit, query)
        retriever = self._vector_store.as_retriever(
            search_type=self._settings.search_type,
            search_kwargs=search_kwargs,
        )
        documents = await retriever.ainvoke(query)
        ranked = [_document_to_result(document) for document in documents]
        expanded = await self._expand_neighbors(ranked, result_limit)
        bounded = _apply_context_budget(expanded, self._settings.max_context_chars)
        return self._response(bounded, limit=result_limit)

    def _search_kwargs(
        self,
        scope_ids: list[str],
        result_limit: int,
        query: str,
    ) -> dict[str, Any]:
        """Build per-call search arguments and mandatory scope filter.

        构建每次调用独立的检索参数及强制作用域过滤器。
        """
        scope_filter: str | dict[str, list[str]] = (
            scope_ids[0] if len(scope_ids) == 1 else {"$in": scope_ids}
        )
        search_kwargs: dict[str, Any] = {
            "k": result_limit,
            "filter": {
                "$and": [
                    {"thread_id": scope_filter},
                    {"is_active": True},
                ]
            },
        }
        if self._settings.search_type == "similarity_score_threshold":
            search_kwargs["score_threshold"] = self._settings.score_threshold
        elif self._settings.search_type == "mmr":
            search_kwargs.update(
                fetch_k=self._settings.fetch_k,
                lambda_mult=self._settings.lambda_mult,
            )
        if self._settings.hybrid.enabled:
            search_kwargs["hybrid_search_config"] = self._hybrid_config(query)
        return search_kwargs

    def _hybrid_config(self, query: str) -> HybridSearchConfig:
        """Create a fresh hybrid config because fusion mutates its parameters.

        每次创建新的混合检索配置，因为融合过程会修改其参数。
        """
        settings = self._settings.hybrid
        if settings.fusion_function == "weighted_sum_ranking":
            fusion_function = weighted_sum_ranking
            parameters = {
                "primary_results_weight": settings.primary_weight,
                "secondary_results_weight": settings.secondary_weight,
            }
        else:
            fusion_function = reciprocal_rank_fusion
            parameters = {"rrf_k": settings.rrf_k}
        return HybridSearchConfig(
            tsv_column="search_vector",
            tsv_lang="pg_catalog.simple",
            fts_query=lexicalize(query),
            fusion_function=fusion_function,
            fusion_function_parameters=parameters,
            primary_top_k=settings.primary_top_k,
            secondary_top_k=settings.secondary_top_k,
        )

    async def _expand_neighbors(
        self,
        ranked: list[KnowledgeSearchResult],
        limit: int,
    ) -> list[KnowledgeSearchResult]:
        """Add local context without changing the order of ranked hits.

        补充局部上下文，同时不改变已排序命中的顺序。
        """
        selected = list(ranked[:limit])
        seen = {result.chunk_id for result in selected}
        for result in ranked:
            if len(selected) >= limit:
                break
            neighbors = await self._repository.adjacent_chunks(
                result.scope_id,
                result.document_id,
                result.ordinal,
            )
            neighbors.sort(key=lambda item: abs(item.ordinal - result.ordinal))
            for neighbor in neighbors:
                if neighbor.chunk_id in seen:
                    continue
                selected.append(neighbor)
                seen.add(neighbor.chunk_id)
                if len(selected) >= limit:
                    break
        return selected

    def _response(
        self,
        results: list[KnowledgeSearchResult],
        *,
        limit: int,
    ) -> dict[str, Any]:
        """Serialize results and effective retrieval metadata. / 序列化结果及生效的检索元数据。"""
        return {
            "search_type": self._settings.search_type,
            "hybrid": self._settings.hybrid.enabled,
            "limit": limit,
            "count": len(results),
            "results": [
                result.to_dict(rank=rank)
                for rank, result in enumerate(results, start=1)
            ],
        }


def _document_to_result(document: Document) -> KnowledgeSearchResult:
    """Map a LangChain document into the public result type. / 将 LangChain 文档映射为公开结果类型。"""
    metadata = document.metadata
    return KnowledgeSearchResult(
        content=document.page_content,
        document_id=str(metadata.get("document_id", "")),
        chunk_id=str(document.id or metadata.get("chunk_id", "")),
        title=str(metadata.get("title", "")),
        source_uri=str(metadata.get("source_uri", "")),
        locator=_mapping(metadata.get("locator")),
        ordinal=int(metadata.get("ordinal", 0)),
        scope_id=str(metadata.get("thread_id", "")),
    )


def _mapping(value: Any) -> dict[str, Any]:
    """Return a plain mapping for optional JSON metadata. / 为可选 JSON 元数据返回普通映射。"""
    return dict(value) if isinstance(value, dict) else {}


def _apply_context_budget(
    results: list[KnowledgeSearchResult],
    max_characters: int,
) -> list[KnowledgeSearchResult]:
    """Bound returned content while preserving at least the first result.

    限制返回内容长度，同时至少保留第一条结果。
    """
    bounded: list[KnowledgeSearchResult] = []
    remaining = max_characters
    for result in results:
        if remaining <= 0:
            break
        content = result.content[:remaining]
        if not content:
            break
        bounded.append(replace(result, content=content))
        remaining -= len(content)
    return bounded
