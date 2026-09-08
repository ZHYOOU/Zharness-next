"""Tests for knowledge ingestion orchestration. / 知识导入编排测试。"""

from datetime import UTC, datetime

import pytest
from zharness.config import KnowledgeChunkingSettings, KnowledgeSettings
from zharness.knowledge.service import KnowledgeService
from zharness.knowledge.types import KnowledgeDocument
from zharness.sandbox.protocol import FileDownloadResponse
from zharness.sandbox.workspace import SandboxWorkspace


class FakeBackend:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.requested: list[list[str]] = []

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        self.requested.append(paths)
        return [
            FileDownloadResponse(path=path, content=self.files.get(path))
            if path in self.files
            else FileDownloadResponse(path=path, error="file_not_found")
            for path in paths
        ]


class FakeRepository:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], KnowledgeDocument] = {}
        self.finalized: list[tuple] = []

    async def ready_source(self, thread_id, source_uri):
        return self.documents.get((thread_id, source_uri))

    async def prepare_document(self, **kwargs):
        return KnowledgeDocument(
            id="doc-1",
            thread_id=kwargs["thread_id"],
            source_uri=kwargs["source_uri"],
            title=kwargs["title"],
            status="indexing",
            chunk_count=0,
            content_hash=kwargs["content_hash"],
            updated_at=datetime.now(UTC),
        )

    async def clear_document_chunks(self, thread_id, document_id):
        return None

    async def finalize_document(self, **kwargs):
        self.finalized.append(
            (
                kwargs["thread_id"],
                kwargs["document_id"],
                kwargs["source_uri"],
                kwargs["chunk_count"],
                kwargs["replace"],
            )
        )

    async def mark_failed(self, thread_id, document_id, error):
        raise AssertionError(error)

    async def list_bindings(self, thread_id):
        return ["base-a"]


class FakeVectorStore:
    def __init__(self) -> None:
        self.documents = []
        self.ids = []

    async def aadd_documents(self, documents, *, ids):
        self.documents.extend(documents)
        self.ids.extend(ids)


class FakeRetriever:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def search(self, thread_id, query, *, limit=None, scope_ids=None):
        self.calls.append((thread_id, query, limit, scope_ids))
        return {"count": 0, "results": []}


@pytest.mark.asyncio
async def test_search_includes_bound_knowledge_base_scopes() -> None:
    repository = FakeRepository()
    retriever = FakeRetriever()
    service = KnowledgeService(repository, KnowledgeSettings())  # type: ignore[arg-type]
    service._repository_ready = True
    service._ready = True
    service._retriever = retriever  # type: ignore[assignment]

    await service.search("thread-a", "reference", limit=3)

    assert retriever.calls == [
        (
            "thread-a",
            "reference",
            3,
            ["thread-a", "knowledge-base:base-a"],
        )
    ]


@pytest.mark.asyncio
async def test_ingest_reads_current_thread_workspace_and_stages_chunks() -> None:
    backend = FakeBackend({"/workspace/notes.md": "# 标题\n\n会话知识内容。".encode()})
    repository = FakeRepository()
    vector_store = FakeVectorStore()
    service = KnowledgeService(
        repository,  # type: ignore[arg-type]
        KnowledgeSettings(
            chunking=KnowledgeChunkingSettings(
                size_characters=20,
                overlap_characters=2,
            )
        ),
        workspace_factory=lambda thread_id: SandboxWorkspace(backend),  # type: ignore[arg-type]
    )
    service._repository_ready = True
    service._ready = True
    service._vector_store = vector_store  # type: ignore[assignment]

    result = await service.ingest("thread-a", ["/workspace/notes.md"])

    assert result["count"] == 1
    assert result["documents"][0]["status"] == "indexed"
    assert backend.requested == [["/workspace/notes.md"]]
    assert vector_store.documents
    assert all(
        document.metadata["thread_id"] == "thread-a"
        for document in vector_store.documents
    )
    assert all(
        document.metadata["is_active"] is False for document in vector_store.documents
    )
    assert repository.finalized[0][0] == "thread-a"


@pytest.mark.asyncio
async def test_ingest_rejects_paths_outside_workspace_before_download() -> None:
    backend = FakeBackend({})
    service = KnowledgeService(
        FakeRepository(),  # type: ignore[arg-type]
        KnowledgeSettings(),
        workspace_factory=lambda thread_id: SandboxWorkspace(backend),  # type: ignore[arg-type]
    )
    service._repository_ready = True
    service._ready = True

    with pytest.raises(Exception, match="under /workspace"):
        await service.ingest("thread-a", ["/etc/passwd"])

    assert backend.requested == []
