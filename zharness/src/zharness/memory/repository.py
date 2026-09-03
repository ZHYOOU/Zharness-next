"""PostgreSQL-backed long-term memory repository. / 基于 PostgreSQL 的长期记忆仓储。

Facts, the user profile, and eviction audits live in three dedicated tables.
Every operation opens its own connection and closes it afterwards, which is
simple, avoids a pool dependency, and is plenty for the single-user scope.

事实、用户画像与驱逐审计分别存放在三张专用表中。每次操作独立开连接并在使用后
关闭：实现简单、无需连接池依赖，且足以满足单用户的使用规模。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation

from zharness.memory.gate import normalize_content
from zharness.memory.types import Fact, MemoryProfile

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5
"""Connection timeout so fail-open paths do not block on an unreachable host. / 连接超时，保证开放失败路径不会因主机不可达而阻塞。"""

_FACT_COLUMNS = (
    "id",
    "content",
    "category",
    "topics",
    "confidence",
    "scope",
    "thread_id",
    "source_type",
    "created_at",
    "updated_at",
    "last_accessed_at",
    "access_count",
    "last_confirmed_at",
    "confirmation_count",
    "revision",
)

_CREATE_FACTS_SQL = """
CREATE TABLE IF NOT EXISTS zharness_memory_facts (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    content_key TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    topics TEXT NOT NULL DEFAULT '[]',
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    scope TEXT NOT NULL DEFAULT 'user',
    thread_id TEXT,
    source_type TEXT NOT NULL DEFAULT 'conversation',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed_at TIMESTAMPTZ,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_confirmed_at TIMESTAMPTZ,
    confirmation_count INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1
)
"""

_MIGRATE_FACTS_SQL = """
ALTER TABLE zharness_memory_facts ADD COLUMN IF NOT EXISTS content_key TEXT NOT NULL DEFAULT '';
UPDATE zharness_memory_facts SET content_key = lower(content) WHERE content_key = '';
"""

_CREATE_PROFILE_SQL = """
CREATE TABLE IF NOT EXISTS zharness_memory_profile (
    user_id TEXT PRIMARY KEY,
    work_context TEXT NOT NULL DEFAULT '',
    personal_context TEXT NOT NULL DEFAULT '',
    top_of_mind TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CREATE_EVICTIONS_SQL = """
CREATE TABLE IF NOT EXISTS zharness_memory_evictions (
    id TEXT PRIMARY KEY,
    fact_id TEXT,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_INDEXES_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_memory_facts_category ON zharness_memory_facts (category)",
    "CREATE INDEX IF NOT EXISTS idx_memory_facts_updated ON zharness_memory_facts (updated_at DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_facts_content_key ON zharness_memory_facts (content_key)",
)

_INSERT_FACT_SQL = """
INSERT INTO zharness_memory_facts (
    id, content, content_key, category, topics, confidence, scope, thread_id,
    source_type, created_at, updated_at, last_accessed_at, access_count,
    last_confirmed_at, confirmation_count, revision
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

_SELECT_FACT_BY_ID_SQL = f"""
SELECT {", ".join(_FACT_COLUMNS)} FROM zharness_memory_facts WHERE id = %s
"""

_SELECT_FACT_ID_BY_CONTENT_KEY_SQL = """
SELECT id FROM zharness_memory_facts WHERE content_key = %s LIMIT 1
"""

_SELECT_ALL_FACTS_SQL = f"""
SELECT {", ".join(_FACT_COLUMNS)} FROM zharness_memory_facts
"""

_DELETE_FACT_SQL = "DELETE FROM zharness_memory_facts WHERE id = %s"

_COUNT_FACTS_SQL = "SELECT count(*) FROM zharness_memory_facts"

_UPDATE_FACT_SQL = """
UPDATE zharness_memory_facts
SET content = %s, content_key = %s, category = %s, confidence = %s, topics = %s,
    updated_at = now(), revision = revision + 1
WHERE id = %s
"""

_RECORD_ACCESS_SQL = """
UPDATE zharness_memory_facts
SET access_count = access_count + 1, last_accessed_at = now()
WHERE id = %s
"""

_RECORD_CONFIRMATION_SQL = """
UPDATE zharness_memory_facts
SET confirmation_count = confirmation_count + 1, last_confirmed_at = now(),
    updated_at = now()
WHERE id = %s
"""

_PROFILE_SELECT_SQL = """
SELECT user_id, work_context, personal_context, top_of_mind, updated_at
FROM zharness_memory_profile WHERE user_id = %s
"""

_PROFILE_UPSERT_SQL = """
INSERT INTO zharness_memory_profile (user_id, work_context, personal_context, top_of_mind)
VALUES (%s, %s, %s, %s)
ON CONFLICT (user_id) DO UPDATE SET
    work_context = EXCLUDED.work_context,
    personal_context = EXCLUDED.personal_context,
    top_of_mind = EXCLUDED.top_of_mind,
    updated_at = now()
"""

_EVICTION_INSERT_SQL = """
INSERT INTO zharness_memory_evictions (id, fact_id, content, category, score, reason)
VALUES (%s, %s, %s, %s, %s, %s)
"""

_EVICTION_PRUNE_SQL = """
DELETE FROM zharness_memory_evictions
WHERE id IN (
    SELECT id FROM zharness_memory_evictions
    ORDER BY created_at DESC OFFSET %s
)
"""

_TOKEN_SPLIT = re.compile(r"\W+")


class MemoryRepository:
    """Persist and query memory facts and the user profile in PostgreSQL.

    在 PostgreSQL 中持久化并查询记忆事实与用户画像。
    """

    def __init__(self, conninfo: str) -> None:
        """Initialize the repository with a connection string. / 使用连接字符串初始化仓储。"""
        self._conninfo = conninfo

    async def setup(self) -> None:
        """Create the memory tables and indexes if absent. / 创建缺失的记忆表与索引。"""
        statements = [
            _CREATE_FACTS_SQL,
            _CREATE_PROFILE_SQL,
            _CREATE_EVICTIONS_SQL,
            _MIGRATE_FACTS_SQL,
        ]
        statements.extend(_INDEXES_SQL)
        for statement in statements:
            await self._execute(statement)

    async def add_fact(self, fact: Fact) -> bool:
        """Insert a fact unless a duplicate content key already exists.

        插入一条事实，除非已存在相同的内容键。

        The SELECT is a fast path; the unique ``content_key`` index is the
        authoritative guard against concurrent duplicate writes.

        SELECT 是快速路径；``content_key`` 的唯一索引是并发重复写入的权威防护。
        """
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    _SELECT_FACT_ID_BY_CONTENT_KEY_SQL,
                    (normalize_content(fact.content),),
                )
                if await cursor.fetchone() is not None:
                    return False
                try:
                    await cursor.execute(
                        _INSERT_FACT_SQL,
                        (
                            fact.id,
                            fact.content,
                            normalize_content(fact.content),
                            fact.category,
                            json.dumps(list(fact.topics), ensure_ascii=False),
                            fact.confidence,
                            fact.scope,
                            fact.thread_id,
                            fact.source_type,
                            fact.created_at,
                            fact.updated_at,
                            fact.last_accessed_at,
                            fact.access_count,
                            fact.last_confirmed_at,
                            fact.confirmation_count,
                            fact.revision,
                        ),
                    )
                except UniqueViolation:
                    return False
            await conn.commit()
        finally:
            await conn.close()
        return True

    async def get_fact(self, fact_id: str) -> Fact | None:
        """Return a fact by id, or ``None`` when missing. / 按 id 返回事实；不存在时返回 ``None``。"""
        rows = await self._fetchall(_SELECT_FACT_BY_ID_SQL, (fact_id,))
        return _row_to_fact(rows[0]) if rows else None

    async def update_fact(
        self,
        fact_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
        topics: Sequence[str] | None = None,
    ) -> bool:
        """Apply a partial update to a fact and bump its revision. / 对事实应用部分更新并递增修订号。"""
        current = await self.get_fact(fact_id)
        if current is None:
            return False
        content = current.content if content is None else content
        category = current.category if category is None else category
        confidence = current.confidence if confidence is None else confidence
        topics = current.topics if topics is None else tuple(topics)
        await self._execute(
            _UPDATE_FACT_SQL,
            (
                content,
                normalize_content(content),
                category,
                confidence,
                json.dumps(list(topics), ensure_ascii=False),
                fact_id,
            ),
        )
        return True

    async def delete_fact(self, fact_id: str) -> bool:
        """Delete a fact by id, returning whether it existed. / 按 id 删除事实，返回是否存在。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(_DELETE_FACT_SQL, (fact_id,))
                deleted = cursor.rowcount > 0
            await conn.commit()
        finally:
            await conn.close()
        return deleted

    async def search_facts(
        self,
        query: str,
        category: str | None = None,
        limit: int = 10,
    ) -> list[Fact]:
        """Search facts by AND-matched token substrings. / 按 AND 匹配的 token 子串搜索事实。"""
        tokens = [token for token in _TOKEN_SPLIT.split(query) if token]
        if not tokens:
            tokens = [query]
        patterns = " AND ".join("strpos(lower(content), %s) > 0" for _ in tokens)
        params: list[Any] = [token.lower() for token in tokens]
        sql = _SELECT_ALL_FACTS_SQL + f" WHERE {patterns}"
        if category:
            sql += " AND category = %s"
            params.append(category)
        sql += " ORDER BY confidence DESC, updated_at DESC LIMIT %s"
        params.append(limit)
        rows = await self._fetchall(sql, params)
        return [_row_to_fact(row) for row in rows]

    async def all_facts(self) -> list[Fact]:
        """Return every stored fact. / 返回所有已存储的事实。"""
        rows = await self._fetchall(_SELECT_ALL_FACTS_SQL)
        return [_row_to_fact(row) for row in rows]

    async def count_facts(self) -> int:
        """Return the total number of stored facts. / 返回已存储事实的总数。"""
        rows = await self._fetchall(_COUNT_FACTS_SQL)
        return int(rows[0][0]) if rows else 0

    async def record_access(self, fact_id: str) -> None:
        """Increment access heat for a fact. / 为一条事实累加访问热度。"""
        await self._execute(_RECORD_ACCESS_SQL, (fact_id,))

    async def record_confirmation(self, fact_id: str) -> None:
        """Record an explicit confirmation for a fact. / 为一条事实记录一次显式确认。"""
        await self._execute(_RECORD_CONFIRMATION_SQL, (fact_id,))

    async def get_profile(self, user_id: str) -> MemoryProfile | None:
        """Return the user profile, or ``None`` when absent. / 返回用户画像；不存在时返回 ``None``。"""
        rows = await self._fetchall(_PROFILE_SELECT_SQL, (user_id,))
        if not rows:
            return None
        _, work_context, personal_context, top_of_mind, updated_at = rows[0]
        return MemoryProfile(
            user_id=user_id,
            work_context=work_context,
            personal_context=personal_context,
            top_of_mind=top_of_mind,
            updated_at=updated_at,
        )

    async def upsert_profile(self, profile: MemoryProfile) -> None:
        """Create or replace the user profile summaries. / 创建或替换用户画像摘要。"""
        await self._execute(
            _PROFILE_UPSERT_SQL,
            (
                profile.user_id,
                profile.work_context,
                profile.personal_context,
                profile.top_of_mind,
            ),
        )

    async def record_eviction(
        self,
        fact: Fact,
        score: float,
        reason: str,
    ) -> None:
        """Audit a capacity eviction. / 记录一次容量驱逐审计。"""
        await self._execute(
            _EVICTION_INSERT_SQL,
            (
                fact.id,
                fact.id,
                fact.content,
                fact.category,
                score,
                reason,
            ),
        )

    async def prune_evictions(self, keep: int = 100) -> None:
        """Trim the eviction audit to the newest ``keep`` entries. / 将驱逐审计裁剪为最新的 ``keep`` 条。"""
        await self._execute(_EVICTION_PRUNE_SQL, (keep,))

    async def close(self) -> None:
        """No-op: every operation opens and closes its own connection. / 空操作：每次操作各自开关连接。"""

    async def _open(self) -> AsyncConnection:
        """Open a new asynchronous PostgreSQL connection. / 打开一个新的异步 PostgreSQL 连接。"""
        return await psycopg.AsyncConnection.connect(
            self._conninfo,
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
        )

    async def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Execute a statement with parameters inside a transaction. / 在事务内执行带参数的语句。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
            await conn.commit()
        finally:
            await conn.close()

    async def _fetchall(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[tuple[Any, ...]]:
        """Execute a query and return every row. / 执行查询并返回所有行。"""
        conn = await self._open()
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                rows = await cursor.fetchall()
            await conn.commit()
        finally:
            await conn.close()
        return rows


def _row_to_fact(row: Sequence[Any]) -> Fact:
    """Map a facts-table row to a :class:`Fact`. / 将 facts 表行映射为 :class:`Fact`。"""
    (
        fact_id,
        content,
        category,
        topics_json,
        confidence,
        scope,
        thread_id,
        source_type,
        created_at,
        updated_at,
        last_accessed_at,
        access_count,
        last_confirmed_at,
        confirmation_count,
        revision,
    ) = row
    try:
        topics = tuple(json.loads(topics_json))
    except (TypeError, ValueError):
        topics = ()
    return Fact(
        id=fact_id,
        content=content,
        category=category,
        topics=topics,
        confidence=confidence,
        scope=scope,
        thread_id=thread_id,
        source_type=source_type,
        created_at=created_at,
        updated_at=updated_at,
        last_accessed_at=last_accessed_at,
        access_count=access_count,
        last_confirmed_at=last_confirmed_at,
        confirmation_count=confirmation_count,
        revision=revision,
    )
