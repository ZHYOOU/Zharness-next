"""Session-scoped RAG knowledge base. / 会话级 RAG 知识库。"""

from zharness.knowledge.service import (
    KnowledgeService,
    KnowledgeUnavailableError,
    close_knowledge_service,
    get_knowledge_service,
)

__all__ = [
    "KnowledgeService",
    "KnowledgeUnavailableError",
    "close_knowledge_service",
    "get_knowledge_service",
]
