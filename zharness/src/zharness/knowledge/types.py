"""Knowledge-base domain types. / 知识库领域类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    """A document chunk prepared for indexing. / 一条已准备好建立索引的文档切片。"""

    id: str
    document_id: str
    thread_id: str
    ordinal: int
    content: str
    lexical_text: str
    title: str
    source_uri: str
    heading_path: tuple[str, ...] = ()
    locator: dict[str, Any] = field(default_factory=dict)
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """Stored knowledge-document metadata. / 已存储的知识文档元数据。"""

    id: str
    thread_id: str
    source_uri: str
    title: str
    status: str
    chunk_count: int
    content_hash: str
    updated_at: Any = None


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    """One ranked result returned by the knowledge retriever. / 知识检索器返回的一条排序结果。"""

    content: str
    document_id: str
    chunk_id: str
    title: str
    source_uri: str
    locator: dict[str, Any]
    ordinal: int
    scope_id: str = ""

    def to_dict(self, *, rank: int) -> dict[str, Any]:
        """Serialize the result with a stable rank. / 使用稳定排名序列化检索结果。"""
        return {
            "rank": rank,
            "content": self.content,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "title": self.title,
            "source_uri": self.source_uri,
            "locator": self.locator,
        }
