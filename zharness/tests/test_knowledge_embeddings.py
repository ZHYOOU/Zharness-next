"""Tests for the Alibaba embedding client factory. / 阿里嵌入客户端工厂测试。"""

import pytest
from zharness.config import KnowledgeEmbeddingSettings
from zharness.knowledge.embeddings import create_embeddings


def test_embedding_factory_uses_dedicated_environment(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embedding.example/v1/")
    monkeypatch.setenv("EMBEDDING_API_KEY", "secret-value")

    embeddings = create_embeddings(
        KnowledgeEmbeddingSettings(
            dimensions=768,
            batch_size=7,
            timeout_seconds=11,
            max_retries=1,
        )
    )

    assert embeddings.model == "text-embedding-v4"
    assert embeddings.dimensions == 768
    assert embeddings.chunk_size == 7
    assert str(embeddings.openai_api_base).rstrip("/") == "https://embedding.example/v1"
    assert "secret-value" not in repr(embeddings)


def test_embedding_factory_rejects_missing_secret(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embedding.example/v1")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="EMBEDDING_API_KEY is required"):
        create_embeddings(KnowledgeEmbeddingSettings())


def test_embedding_factory_rejects_full_endpoint(monkeypatch) -> None:
    monkeypatch.setenv(
        "EMBEDDING_BASE_URL",
        "https://embedding.example/v1/embeddings",
    )
    monkeypatch.setenv("EMBEDDING_API_KEY", "secret-value")

    with pytest.raises(ValueError, match="must not include"):
        create_embeddings(KnowledgeEmbeddingSettings())
