"""PostgreSQL persistence for the session knowledge base. / 会话知识库的 PostgreSQL 持久化层。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import AsyncConnection

from zharness.knowledge.types import KnowledgeDocument, KnowledgeSearchResult

_CONNECT_TIMEOUT_SECONDS = 5


class KnowledgeRepository:
    """Persist document lifecycle metadata around a PGVectorStore table.

    围绕 PGVectorStore 表持久化文档生命周期元数据。
    """

    def __init__(
        self,
        conninfo: str,
        *,
        dimensions: int = 1024,
        embedding_model: str | None = None,
    ) -> None:
        """Initialize the repository and fixed vector dimension. / 初始化仓储和固定向量维度。"""
        if dimensions < 1:
            raise ValueError("knowledge vector dimensions must be positive")
        self._conninfo = conninfo
        self._dimensions = dimensions
        self._embedding_model = embedding_model

    async def setup(self) -> None:
        """Create the vector extension, tables, and indexes. / 创建向量扩展、数据表和索引。"""
        statements = (
            "CREATE EXTENSION IF NOT EXISTS vector",
            """
            CREATE TABLE IF NOT EXISTS zharness_knowledge_documents (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                title TEXT NOT NULL,
                mime_type TEXT,
                content_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                embedding_model TEXT NOT NULL,
                embedding_dimensions INTEGER NOT NULL,
                index_version INTEGER NOT NULL DEFAULT 1,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (thread_id, source_uri, content_hash)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS zharness_knowledge_bases (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS zharness_knowledge_bindings (
                thread_id TEXT NOT NULL,
                knowledge_base_id TEXT NOT NULL
                    REFERENCES zharness_knowledge_bases(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (thread_id, knowledge_base_id)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS zharness_knowledge_chunks (
                id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL,
                document_id TEXT NOT NULL REFERENCES zharness_knowledge_documents(id)
                    ON DELETE CASCADE,
                is_active BOOLEAN NOT NULL DEFAULT FALSE,
                ordinal INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding vector({self._dimensions}) NOT NULL,
                lexical_text TEXT NOT NULL,
                search_vector TSVECTOR GENERATED ALWAYS AS (
                    to_tsvector('pg_catalog.simple'::regconfig, lexical_text)
                ) STORED,
                title TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                heading_path JSONB NOT NULL DEFAULT '[]'::jsonb,
                locator JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                content_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (document_id, ordinal)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_knowledge_documents_thread ON zharness_knowledge_documents (thread_id, updated_at DESC)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_documents_ready_source ON zharness_knowledge_documents (thread_id, source_uri) WHERE status = 'ready'",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_scope ON zharness_knowledge_chunks (thread_id, is_active)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document ON zharness_knowledge_chunks (thread_id, document_id, ordinal)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_search ON zharness_knowledge_chunks USING GIN (search_vector)",
        )
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                for statement in statements:
                    await cursor.execute(statement)
                await self._validate_embedding_schema(cursor)
            await conn.commit()
        finally:
            await conn.close()

    async def ready_source(
        self,
        thread_id: str,
        source_uri: str,
    ) -> KnowledgeDocument | None:
        """Return the ready document for a source in one thread. / 返回某线程来源对应的 ready 文档。"""
        rows = await self._fetchall(
            """
            SELECT id, thread_id, source_uri, title, status, chunk_count,
                   content_hash, updated_at
            FROM zharness_knowledge_documents
            WHERE thread_id = %s AND source_uri = %s AND status = 'ready'
            LIMIT 1
            """,
            (thread_id, source_uri),
        )
        return _row_to_document(rows[0]) if rows else None

    async def prepare_document(
        self,
        *,
        thread_id: str,
        source_uri: str,
        title: str,
        mime_type: str | None,
        content_hash: str,
        embedding_model: str,
        embedding_dimensions: int,
    ) -> KnowledgeDocument:
        """Create or restart an idempotent indexing record. / 创建或重新启动一条幂等索引记录。"""
        document_id = uuid.uuid4().hex
        rows = await self._fetchall(
            """
            INSERT INTO zharness_knowledge_documents (
                id, thread_id, source_type, source_uri, title, mime_type,
                content_hash, status, embedding_model, embedding_dimensions
            )
            VALUES (%s, %s, 'workspace_file', %s, %s, %s, %s, 'indexing', %s, %s)
            ON CONFLICT (thread_id, source_uri, content_hash) DO UPDATE SET
                status = CASE
                    WHEN zharness_knowledge_documents.status = 'ready' THEN 'ready'
                    ELSE 'indexing'
                END,
                error = NULL,
                updated_at = now()
            RETURNING id, thread_id, source_uri, title, status, chunk_count,
                      content_hash, updated_at
            """,
            (
                document_id,
                thread_id,
                source_uri,
                title,
                mime_type,
                content_hash,
                embedding_model,
                embedding_dimensions,
            ),
        )
        return _row_to_document(rows[0])

    async def clear_document_chunks(self, thread_id: str, document_id: str) -> None:
        """Delete inactive chunks before an idempotent retry. / 在幂等重试前删除未激活切片。"""
        await self._execute(
            "DELETE FROM zharness_knowledge_chunks WHERE thread_id = %s AND document_id = %s AND is_active = FALSE",
            (thread_id, document_id),
        )

    async def finalize_document(
        self,
        *,
        thread_id: str,
        document_id: str,
        source_uri: str,
        chunk_count: int,
        replace: bool,
    ) -> None:
        """Atomically publish a completed document version. / 原子发布已完成的文档版本。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                if replace:
                    await cursor.execute(
                        """
                        UPDATE zharness_knowledge_documents
                        SET status = 'superseded', updated_at = now()
                        WHERE thread_id = %s AND source_uri = %s
                          AND id <> %s AND status = 'ready'
                        """,
                        (thread_id, source_uri, document_id),
                    )
                    await cursor.execute(
                        """
                        UPDATE zharness_knowledge_chunks
                        SET is_active = FALSE
                        WHERE thread_id = %s AND source_uri = %s
                          AND document_id <> %s
                        """,
                        (thread_id, source_uri, document_id),
                    )
                await cursor.execute(
                    """
                    UPDATE zharness_knowledge_chunks
                    SET is_active = TRUE
                    WHERE thread_id = %s AND document_id = %s
                    """,
                    (thread_id, document_id),
                )
                await cursor.execute(
                    """
                    UPDATE zharness_knowledge_documents
                    SET status = 'ready', chunk_count = %s, error = NULL,
                        updated_at = now()
                    WHERE thread_id = %s AND id = %s AND status = 'indexing'
                    """,
                    (chunk_count, thread_id, document_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("knowledge document was not in indexing state")
                if replace:
                    await cursor.execute(
                        """
                        DELETE FROM zharness_knowledge_documents
                        WHERE thread_id = %s AND source_uri = %s
                          AND id <> %s AND status = 'superseded'
                        """,
                        (thread_id, source_uri, document_id),
                    )
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()

    async def mark_failed(
        self,
        thread_id: str,
        document_id: str,
        error: str,
    ) -> None:
        """Hide partial chunks and mark an indexing attempt failed. / 隐藏部分切片并标记索引尝试失败。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM zharness_knowledge_chunks WHERE thread_id = %s AND document_id = %s AND is_active = FALSE",
                    (thread_id, document_id),
                )
                await cursor.execute(
                    """
                    UPDATE zharness_knowledge_documents
                    SET status = 'failed', error = %s, updated_at = now()
                    WHERE thread_id = %s AND id = %s AND status <> 'ready'
                    """,
                    (error[:2000], thread_id, document_id),
                )
            await conn.commit()
        finally:
            await conn.close()

    async def list_documents(self, thread_id: str) -> list[KnowledgeDocument]:
        """List documents visible to one thread. / 列出某线程可见的文档。"""
        rows = await self._fetchall(
            """
            SELECT id, thread_id, source_uri, title, status, chunk_count,
                   content_hash, updated_at
            FROM zharness_knowledge_documents
            WHERE thread_id = %s AND status <> 'superseded'
            ORDER BY updated_at DESC
            """,
            (thread_id,),
        )
        return [_row_to_document(row) for row in rows]

    async def list_knowledge_bases(self) -> list[dict[str, Any]]:
        """List reusable knowledge bases and document counts. / 列出可复用知识库及文档数量。"""
        rows = await self._fetchall(
            """
            SELECT base.id, base.name, base.description, base.created_at,
                   base.updated_at, COUNT(document.id)
            FROM zharness_knowledge_bases AS base
            LEFT JOIN zharness_knowledge_documents AS document
              ON document.thread_id = 'knowledge-base:' || base.id
             AND document.status <> 'superseded'
            GROUP BY base.id
            ORDER BY base.updated_at DESC, base.name
            """
        )
        return [
            {
                "id": str(row[0]),
                "name": str(row[1]),
                "description": str(row[2]),
                "created_at": row[3],
                "updated_at": row[4],
                "document_count": int(row[5]),
            }
            for row in rows
        ]

    async def create_knowledge_base(
        self, knowledge_base_id: str, name: str, description: str
    ) -> None:
        """Create reusable knowledge-base metadata. / 创建可复用知识库元数据。"""
        await self._execute(
            "INSERT INTO zharness_knowledge_bases (id, name, description) VALUES (%s, %s, %s)",
            (knowledge_base_id, name, description),
        )

    async def update_knowledge_base(
        self, knowledge_base_id: str, name: str, description: str
    ) -> bool:
        """Update one reusable knowledge base. / 更新一个可复用知识库。"""
        return (
            await self._mutate_count(
                """
                UPDATE zharness_knowledge_bases
                SET name = %s, description = %s, updated_at = now()
                WHERE id = %s
                """,
                (name, description, knowledge_base_id),
            )
            > 0
        )

    async def delete_knowledge_base(self, knowledge_base_id: str) -> bool:
        """Delete metadata and indexed documents for one base. / 删除一个知识库的元数据及索引文档。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM zharness_knowledge_documents WHERE thread_id = %s",
                    (f"knowledge-base:{knowledge_base_id}",),
                )
                await cursor.execute(
                    "DELETE FROM zharness_knowledge_bases WHERE id = %s",
                    (knowledge_base_id,),
                )
                deleted = cursor.rowcount > 0
            await conn.commit()
        finally:
            await conn.close()
        return deleted

    async def knowledge_base_exists(self, knowledge_base_id: str) -> bool:
        """Return whether a knowledge base exists. / 返回知识库是否存在。"""
        rows = await self._fetchall(
            "SELECT 1 FROM zharness_knowledge_bases WHERE id = %s LIMIT 1",
            (knowledge_base_id,),
        )
        return bool(rows)

    async def list_bindings(self, thread_id: str) -> list[str]:
        """List knowledge bases bound to a thread. / 列出会话绑定的知识库。"""
        rows = await self._fetchall(
            """
            SELECT knowledge_base_id FROM zharness_knowledge_bindings
            WHERE thread_id = %s ORDER BY created_at
            """,
            (thread_id,),
        )
        return [str(row[0]) for row in rows]

    async def set_bindings(self, thread_id: str, knowledge_base_ids: list[str]) -> None:
        """Replace all knowledge-base bindings for a thread. / 替换会话的全部知识库绑定。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM zharness_knowledge_bindings WHERE thread_id = %s",
                    (thread_id,),
                )
                if knowledge_base_ids:
                    await cursor.executemany(
                        """
                        INSERT INTO zharness_knowledge_bindings
                            (thread_id, knowledge_base_id)
                        VALUES (%s, %s)
                        """,
                        [(thread_id, item) for item in knowledge_base_ids],
                    )
            await conn.commit()
        finally:
            await conn.close()

    async def delete_document(self, thread_id: str, document_id: str) -> bool:
        """Delete one document inside its owning thread. / 在所属线程内删除单个文档。"""
        return await self._delete(
            "DELETE FROM zharness_knowledge_documents WHERE thread_id = %s AND id = %s",
            (thread_id, document_id),
        )

    async def delete_thread(self, thread_id: str) -> int:
        """Delete every knowledge document owned by a thread. / 删除某线程拥有的全部知识文档。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM zharness_knowledge_bindings WHERE thread_id = %s",
                    (thread_id,),
                )
                await cursor.execute(
                    "DELETE FROM zharness_knowledge_documents WHERE thread_id = %s",
                    (thread_id,),
                )
                count = cursor.rowcount
            await conn.commit()
        finally:
            await conn.close()
        return count

    async def adjacent_chunks(
        self,
        thread_id: str,
        document_id: str,
        ordinal: int,
        *,
        radius: int = 1,
    ) -> list[KnowledgeSearchResult]:
        """Return active neighboring chunks around one hit. / 返回某个命中附近的活跃切片。"""
        rows = await self._fetchall(
            """
            SELECT content, document_id, id, title, source_uri, locator, ordinal,
                   thread_id
            FROM zharness_knowledge_chunks
            WHERE thread_id = %s AND document_id = %s AND is_active = TRUE
              AND ordinal BETWEEN %s AND %s
            ORDER BY ordinal
            """,
            (thread_id, document_id, ordinal - radius, ordinal + radius),
        )
        return [_row_to_search_result(row) for row in rows]

    async def close(self) -> None:
        """No-op because operations use short-lived connections. / 空操作，因为各操作使用短生命周期连接。"""

    async def _validate_embedding_schema(self, cursor: Any) -> None:
        """Reject vector dimensions or models incompatible with ready data.

        拒绝与既有 ready 数据不兼容的向量维度或模型。
        """
        await cursor.execute(
            """
            SELECT format_type(attribute.atttypid, attribute.atttypmod)
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'zharness_knowledge_chunks'::regclass
              AND attribute.attname = 'embedding'
              AND NOT attribute.attisdropped
            """
        )
        row = await cursor.fetchone()
        expected_type = f"vector({self._dimensions})"
        if row is None or str(row[0]) != expected_type:
            actual_type = str(row[0]) if row is not None else "missing"
            raise RuntimeError(
                f"knowledge embedding column is {actual_type}, expected {expected_type}"
            )
        if self._embedding_model is None:
            return
        await cursor.execute(
            """
            SELECT DISTINCT embedding_model, embedding_dimensions
            FROM zharness_knowledge_documents
            WHERE status = 'ready'
              AND (embedding_model <> %s OR embedding_dimensions <> %s)
            LIMIT 1
            """,
            (self._embedding_model, self._dimensions),
        )
        incompatible = await cursor.fetchone()
        if incompatible is not None:
            raise RuntimeError(
                "ready knowledge was indexed with an incompatible embedding "
                "model or dimension; reindex it before startup"
            )

    async def _open(self) -> AsyncConnection:
        """Open one asynchronous psycopg connection. / 打开一个异步 psycopg 连接。"""
        return await psycopg.AsyncConnection.connect(
            self._conninfo,
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        )

    async def _execute(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> None:
        """Execute a mutating statement. / 执行一条修改语句。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
            await conn.commit()
        finally:
            await conn.close()

    async def _delete(self, sql: str, params: Sequence[Any]) -> bool:
        """Execute a delete and report whether a row existed. / 执行删除并报告是否存在记录。"""
        return await self._delete_count(sql, params) > 0

    async def _delete_count(self, sql: str, params: Sequence[Any]) -> int:
        """Execute a delete and return its affected-row count. / 执行删除并返回影响行数。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                count = cursor.rowcount
            await conn.commit()
        finally:
            await conn.close()
        return count

    async def _mutate_count(self, sql: str, params: Sequence[Any]) -> int:
        """Execute a mutation and return its affected-row count. / 执行修改并返回影响行数。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                count = cursor.rowcount
            await conn.commit()
        finally:
            await conn.close()
        return count

    async def _fetchall(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> list[tuple[Any, ...]]:
        """Execute a query and return all rows. / 执行查询并返回全部记录。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
            await conn.commit()
        finally:
            await conn.close()
        return rows


def _row_to_document(row: Sequence[Any]) -> KnowledgeDocument:
    """Map a database row to document metadata. / 将数据库记录映射为文档元数据。"""
    return KnowledgeDocument(
        id=str(row[0]),
        thread_id=str(row[1]),
        source_uri=str(row[2]),
        title=str(row[3]),
        status=str(row[4]),
        chunk_count=int(row[5]),
        content_hash=str(row[6]),
        updated_at=row[7],
    )


def _row_to_search_result(row: Sequence[Any]) -> KnowledgeSearchResult:
    """Map a chunk row to a search result. / 将切片记录映射为检索结果。"""
    return KnowledgeSearchResult(
        content=str(row[0]),
        document_id=str(row[1]),
        chunk_id=str(row[2]),
        title=str(row[3]),
        source_uri=str(row[4]),
        locator=dict(row[5] or {}),
        ordinal=int(row[6]),
        scope_id=str(row[7]) if len(row) > 7 else "",
    )
