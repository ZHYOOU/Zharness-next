"""Session knowledge-base orchestration. / 会话知识库编排层。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

from langchain_core.documents import Document
from langchain_postgres import PGEngine, PGVectorStore
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from zharness.config import KnowledgeSettings, get_settings
from zharness.knowledge.chunking import split_document
from zharness.knowledge.embeddings import create_embeddings
from zharness.knowledge.repository import KnowledgeRepository
from zharness.knowledge.retrieval import KnowledgeRetriever
from zharness.knowledge.types import KnowledgeChunk
from zharness.sandbox.manager import get_sandbox_manager
from zharness.sandbox.workspace import SandboxWorkspace
from zharness.server.database import postgres_uri

logger = logging.getLogger(__name__)

_CHUNK_TABLE = "zharness_knowledge_chunks"
_METADATA_COLUMNS = [
    "thread_id",
    "document_id",
    "is_active",
    "ordinal",
    "lexical_text",
    "title",
    "source_uri",
    "heading_path",
    "locator",
    "content_hash",
]


class KnowledgeUnavailableError(RuntimeError):
    """Raised when knowledge storage or embedding is unavailable. / 知识存储或嵌入服务不可用时抛出。"""


class KnowledgeService:
    """Index, retrieve, and manage knowledge bound to a thread.

    索引、检索并管理绑定到线程的知识。
    """

    def __init__(
        self,
        repository: KnowledgeRepository,
        settings: KnowledgeSettings,
        *,
        workspace_factory: Callable[[str], SandboxWorkspace] | None = None,
    ) -> None:
        """Initialize lazy infrastructure and injectable workspace access.

        初始化延迟加载的基础设施及可注入的工作区访问。
        """
        self._repository = repository
        self._settings = settings
        self._workspace_factory = workspace_factory or _workspace_for_thread
        self._repository_ready = False
        self._ready = False
        self._lock = asyncio.Lock()
        self._pg_engine: PGEngine | None = None
        self._sqlalchemy_engine: AsyncEngine | None = None
        self._vector_store: PGVectorStore | None = None
        self._retriever: KnowledgeRetriever | None = None

    async def initialize(self) -> None:
        """Validate storage and embedding infrastructure at startup.

        在启动时校验存储和嵌入基础设施。
        """
        await self._ensure_ready()

    async def ingest(
        self,
        thread_id: str,
        paths: list[str],
        *,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Index UTF-8 workspace files for one thread. / 为单个线程索引 UTF-8 工作区文件。"""
        if not paths:
            return {"count": 0, "documents": [], "error": "paths must not be empty"}
        if len(paths) > self._settings.limits.max_files_per_call:
            return {
                "count": 0,
                "documents": [],
                "error": (
                    "too many files; maximum is "
                    f"{self._settings.limits.max_files_per_call}"
                ),
            }
        await self._ensure_ready()
        workspace = self._workspace_factory(thread_id)
        try:
            canonical_paths = [workspace.canonical_path(path) for path in paths]
            responses = await workspace.backend.adownload_files(canonical_paths)
        except Exception as exc:
            raise KnowledgeUnavailableError(str(exc)) from exc

        outcomes: list[dict[str, Any]] = []
        for path, response in zip(canonical_paths, responses, strict=True):
            if response.error is not None:
                outcomes.append(
                    {"source_uri": path, "status": "error", "error": response.error}
                )
                continue
            if response.content is None:
                outcomes.append(
                    {
                        "source_uri": path,
                        "status": "error",
                        "error": "file returned no content",
                    }
                )
                continue
            outcomes.append(
                await self._ingest_content(
                    thread_id,
                    path,
                    response.content,
                    replace=replace,
                )
            )
        success_count = sum(
            outcome["status"] in {"indexed", "duplicate"} for outcome in outcomes
        )
        return {"count": success_count, "documents": outcomes}

    async def search(
        self,
        thread_id: str,
        query: str,
        *,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Search knowledge owned by one thread. / 检索某个线程拥有的知识。"""
        await self._ensure_ready()
        if self._retriever is None:
            raise KnowledgeUnavailableError("knowledge retriever is unavailable")
        try:
            return await self._retriever.search(thread_id, query, limit=limit)
        except Exception as exc:
            logger.exception("Knowledge search failed")
            raise KnowledgeUnavailableError(str(exc)) from exc

    async def list_documents(self, thread_id: str) -> dict[str, Any]:
        """List documents owned by one thread. / 列出某个线程拥有的文档。"""
        await self._ensure_repository_ready()
        try:
            documents = await self._repository.list_documents(thread_id)
        except Exception as exc:
            raise KnowledgeUnavailableError(str(exc)) from exc
        values = [
            {
                "id": document.id,
                "source_uri": document.source_uri,
                "title": document.title,
                "status": document.status,
                "chunk_count": document.chunk_count,
                "content_hash": document.content_hash,
                "updated_at": _serialize_time(document.updated_at),
            }
            for document in documents
        ]
        return {"count": len(values), "documents": values}

    async def delete_document(self, thread_id: str, document_id: str) -> dict[str, Any]:
        """Delete a document only within its owning thread. / 仅在所属线程内删除文档。"""
        await self._ensure_repository_ready()
        try:
            deleted = await self._repository.delete_document(thread_id, document_id)
        except Exception as exc:
            raise KnowledgeUnavailableError(str(exc)) from exc
        if not deleted:
            return {"error": "document not found"}
        return {"id": document_id, "status": "deleted"}

    async def delete_thread(self, thread_id: str) -> int:
        """Delete all knowledge for a deleted thread. / 删除已移除线程的全部知识。"""
        await self._ensure_repository_ready()
        try:
            return await self._repository.delete_thread(thread_id)
        except Exception as exc:
            raise KnowledgeUnavailableError(str(exc)) from exc

    async def close(self) -> None:
        """Close lazy database resources. / 关闭延迟创建的数据库资源。"""
        if self._pg_engine is not None:
            await self._pg_engine.close()
        elif self._sqlalchemy_engine is not None:
            await self._sqlalchemy_engine.dispose()
        await self._repository.close()
        self._pg_engine = None
        self._sqlalchemy_engine = None
        self._vector_store = None
        self._retriever = None
        self._repository_ready = False
        self._ready = False

    async def _ingest_content(
        self,
        thread_id: str,
        source_uri: str,
        content: bytes,
        *,
        replace: bool,
    ) -> dict[str, Any]:
        """Index one downloaded file with idempotent publication. / 以幂等发布方式索引一个已下载文件。"""
        if len(content) > self._settings.limits.max_file_bytes:
            return {
                "source_uri": source_uri,
                "status": "error",
                "error": f"file exceeds {self._settings.limits.max_file_bytes}-byte limit",
            }
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "source_uri": source_uri,
                "status": "error",
                "error": "file is not UTF-8 text",
            }
        if not text.strip():
            return {
                "source_uri": source_uri,
                "status": "error",
                "error": "file is empty",
            }

        content_hash = hashlib.sha256(content).hexdigest()
        existing = await self._repository.ready_source(thread_id, source_uri)
        if existing is not None and existing.content_hash == content_hash:
            return _document_outcome(
                existing.id, source_uri, "duplicate", existing.chunk_count
            )
        if existing is not None and not replace:
            return {
                "source_uri": source_uri,
                "status": "error",
                "error": "source already exists; set replace=true to replace it",
            }

        title = PurePosixPath(source_uri).name
        mime_type = mimetypes.guess_type(source_uri)[0] or "text/plain"
        record = await self._repository.prepare_document(
            thread_id=thread_id,
            source_uri=source_uri,
            title=title,
            mime_type=mime_type,
            content_hash=content_hash,
            embedding_model=self._settings.embedding.model,
            embedding_dimensions=self._settings.embedding.dimensions,
        )
        if record.status == "ready":
            return _document_outcome(
                record.id, source_uri, "duplicate", record.chunk_count
            )

        try:
            await self._repository.clear_document_chunks(thread_id, record.id)
            chunks = split_document(
                text,
                thread_id=thread_id,
                document_id=record.id,
                source_uri=source_uri,
                title=title,
                settings=self._settings.chunking,
            )
            if not chunks:
                raise ValueError("document produced no chunks")
            if len(chunks) > self._settings.limits.max_chunks_per_document:
                raise ValueError(
                    "document exceeds "
                    f"{self._settings.limits.max_chunks_per_document}-chunk limit"
                )
            if self._vector_store is None:
                raise RuntimeError("knowledge vector store is unavailable")
            documents = [_chunk_to_document(chunk) for chunk in chunks]
            await self._vector_store.aadd_documents(
                documents,
                ids=[chunk.id for chunk in chunks],
            )
            await self._repository.finalize_document(
                thread_id=thread_id,
                document_id=record.id,
                source_uri=source_uri,
                chunk_count=len(chunks),
                replace=replace,
            )
        except Exception as exc:
            logger.exception("Failed to index knowledge source %s", source_uri)
            try:
                await self._repository.mark_failed(thread_id, record.id, str(exc))
            except Exception:
                logger.exception(
                    "Failed to record knowledge indexing failure for %s",
                    source_uri,
                )
            return {"source_uri": source_uri, "status": "error", "error": str(exc)}
        return _document_outcome(record.id, source_uri, "indexed", len(chunks))

    async def _ensure_repository_ready(self) -> None:
        """Create knowledge tables once per process. / 每个进程仅创建一次知识库表。"""
        if self._repository_ready:
            return
        async with self._lock:
            if self._repository_ready:
                return
            try:
                await self._repository.setup()
            except Exception as exc:
                raise KnowledgeUnavailableError(str(exc)) from exc
            self._repository_ready = True

    async def _ensure_ready(self) -> None:
        """Create vector infrastructure on first retrieval or ingestion. / 首次检索或导入时创建向量基础设施。"""
        if self._ready:
            return
        await self._ensure_repository_ready()
        async with self._lock:
            if self._ready:
                return
            sqlalchemy_engine: AsyncEngine | None = None
            pg_engine: PGEngine | None = None
            try:
                embeddings = create_embeddings(self._settings.embedding)
                sqlalchemy_engine = create_async_engine(
                    _asyncpg_uri(postgres_uri()),
                    pool_pre_ping=True,
                )
                pg_engine = PGEngine.from_engine(sqlalchemy_engine)
                vector_store = await PGVectorStore.create(
                    pg_engine,
                    embeddings,
                    table_name=_CHUNK_TABLE,
                    content_column="content",
                    embedding_column="embedding",
                    metadata_columns=_METADATA_COLUMNS,
                    id_column="id",
                    metadata_json_column=None,
                    k=self._settings.retrieval.result_limit,
                    fetch_k=self._settings.retrieval.fetch_k,
                    lambda_mult=self._settings.retrieval.lambda_mult,
                )
            except Exception as exc:
                if pg_engine is not None:
                    await pg_engine.close()
                elif sqlalchemy_engine is not None:
                    await sqlalchemy_engine.dispose()
                logger.exception("Failed to initialize knowledge vector store")
                raise KnowledgeUnavailableError(str(exc)) from exc
            self._sqlalchemy_engine = sqlalchemy_engine
            self._pg_engine = pg_engine
            self._vector_store = vector_store
            self._retriever = KnowledgeRetriever(
                vector_store,
                self._repository,
                self._settings.retrieval,
            )
            self._ready = True


def _chunk_to_document(chunk: KnowledgeChunk) -> Document:
    """Convert an indexed chunk to a LangChain document. / 将待索引切片转换为 LangChain 文档。"""
    return Document(
        page_content=chunk.content,
        metadata={
            "thread_id": chunk.thread_id,
            "document_id": chunk.document_id,
            "is_active": False,
            "ordinal": chunk.ordinal,
            "lexical_text": chunk.lexical_text,
            "title": chunk.title,
            "source_uri": chunk.source_uri,
            "heading_path": json.dumps(
                list(chunk.heading_path),
                ensure_ascii=False,
            ),
            "locator": chunk.locator,
            "content_hash": chunk.content_hash,
        },
    )


def _workspace_for_thread(thread_id: str) -> SandboxWorkspace:
    """Open the sandbox workspace owned by one thread. / 打开某个线程拥有的沙箱工作区。"""
    return SandboxWorkspace(get_sandbox_manager().for_thread(thread_id))


def _asyncpg_uri(conninfo: str) -> str:
    """Convert a PostgreSQL URI into SQLAlchemy's asyncpg URI. / 将 PostgreSQL URI 转为 SQLAlchemy asyncpg URI。"""
    url = make_url(conninfo)
    return url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


def _serialize_time(value: Any) -> str | None:
    """Serialize an optional database timestamp. / 序列化可选的数据库时间戳。"""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _document_outcome(
    document_id: str,
    source_uri: str,
    status: str,
    chunk_count: int,
) -> dict[str, Any]:
    """Build a stable per-document ingestion result. / 构建稳定的单文档导入结果。"""
    return {
        "id": document_id,
        "source_uri": source_uri,
        "status": status,
        "chunk_count": chunk_count,
    }


_KNOWLEDGE_SERVICE: KnowledgeService | None = None


def get_knowledge_service() -> KnowledgeService:
    """Return the process-wide lazy knowledge service. / 返回进程级延迟知识服务。"""
    global _KNOWLEDGE_SERVICE
    if _KNOWLEDGE_SERVICE is None:
        settings = get_settings().knowledge
        _KNOWLEDGE_SERVICE = KnowledgeService(
            KnowledgeRepository(
                postgres_uri(),
                dimensions=settings.embedding.dimensions,
                embedding_model=settings.embedding.model,
            ),
            settings,
        )
    return _KNOWLEDGE_SERVICE


async def close_knowledge_service() -> None:
    """Close and clear the process-wide knowledge service. / 关闭并清除进程级知识服务。"""
    global _KNOWLEDGE_SERVICE
    service = _KNOWLEDGE_SERVICE
    _KNOWLEDGE_SERVICE = None
    if service is not None:
        await service.close()
