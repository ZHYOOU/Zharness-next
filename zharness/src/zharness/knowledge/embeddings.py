"""Embedding provider factory for the knowledge base. / 知识库嵌入模型工厂。"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from zharness.config import KnowledgeEmbeddingSettings

EMBEDDING_BASE_URL_ENV = "EMBEDDING_BASE_URL"
EMBEDDING_API_KEY_ENV = "EMBEDDING_API_KEY"


def create_embeddings(settings: KnowledgeEmbeddingSettings) -> Embeddings:
    """Create the OpenAI-compatible Alibaba embedding client.

    创建兼容 OpenAI 协议的阿里嵌入客户端。
    """
    base_url = os.environ.get(EMBEDDING_BASE_URL_ENV, "").strip().rstrip("/")
    api_key = os.environ.get(EMBEDDING_API_KEY_ENV, "").strip()
    if not base_url:
        raise RuntimeError(f"{EMBEDDING_BASE_URL_ENV} is required")
    if not api_key:
        raise RuntimeError(f"{EMBEDDING_API_KEY_ENV} is required")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{EMBEDDING_BASE_URL_ENV} must be an HTTP(S) API root")
    if parsed.path.rstrip("/").endswith("/embeddings"):
        raise ValueError(
            f"{EMBEDDING_BASE_URL_ENV} must not include the /embeddings endpoint"
        )
    return OpenAIEmbeddings(
        model=settings.model,
        dimensions=settings.dimensions,
        api_key=api_key,
        base_url=base_url,
        chunk_size=settings.batch_size,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
        check_embedding_ctx_length=False,
    )
