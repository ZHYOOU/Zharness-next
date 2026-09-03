"""Tests for the PostgreSQL memory repository SQL layer. / PostgreSQL 记忆仓储 SQL 层的测试。"""

from __future__ import annotations

from typing import Self

import pytest
from zharness.memory import repository as repository_module
from zharness.memory.repository import MemoryRepository
from zharness.memory.types import Fact, MemoryProfile, utcnow


class _FakeCursor:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn
        self.executed: list[tuple[str, tuple]] = []
        self._fetch_index = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def execute(self, sql: str, params: tuple | None = None) -> None:
        self.executed.append((sql, params or ()))
        self._fetch_index = 0

    @property
    def rowcount(self) -> int:
        if not self.executed:
            return 0
        sql = self.executed[-1][0]
        if sql.startswith("DELETE FROM zharness_memory_facts"):
            return 1 if self.executed[-1][1][0] in self.conn.deleted else 0
        return 1

    async def fetchone(self):
        if self._fetch_index < len(self.conn.rows):
            row = self.conn.rows[self._fetch_index]
            self._fetch_index += 1
            return row
        return None

    async def fetchall(self):
        return self.conn.rows


class _FakeConnection:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.rows = rows or []
        self.deleted: set[str] = set()
        self.cursors: list[_FakeCursor] = []

    def cursor(self) -> _FakeCursor:
        cursor = _FakeCursor(self)
        self.cursors.append(cursor)
        return cursor

    async def commit(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _repository(
    fake: _FakeConnection,
    conninfo: str = "postgresql://x:y@localhost/db",
    monkeypatch=None,
) -> MemoryRepository:
    repo = MemoryRepository(conninfo)

    async def connect(conninfo: str, **kwargs):
        return fake

    monkeypatch.setattr(repository_module.psycopg.AsyncConnection, "connect", connect)
    return repo


@pytest.mark.asyncio
async def test_setup_creates_tables(monkeypatch) -> None:
    fake = _FakeConnection()
    repo = _repository(fake, monkeypatch=monkeypatch)

    await repo.setup()

    statements = [sql for cursor in fake.cursors for sql, _ in cursor.executed]
    assert any(
        "zharness_memory_facts" in sql and "CREATE TABLE" in sql for sql in statements
    )
    assert any("zharness_memory_profile" in sql for sql in statements)
    assert any("zharness_memory_evictions" in sql for sql in statements)
    assert any(
        "content_key" in sql and "ADD COLUMN IF NOT EXISTS" in sql for sql in statements
    )
    assert any(
        "idx_memory_facts_content_key" in sql and "UNIQUE" in sql for sql in statements
    )
    assert any("idx_memory_facts_category" in sql for sql in statements)


@pytest.mark.asyncio
async def test_add_fact_inserts_and_detects_duplicates(monkeypatch) -> None:
    fake = _FakeConnection()
    repo = _repository(fake, monkeypatch=monkeypatch)
    now = utcnow()
    fact = Fact(
        id="abc123",
        content="Prefers Python",
        category="preference",
        confidence=0.9,
        created_at=now,
        updated_at=now,
    )

    added = await repo.add_fact(fact)
    assert added is True
    insert_sql, params = fake.cursors[0].executed[-1]
    assert "INSERT INTO zharness_memory_facts" in insert_sql
    assert params[0] == "abc123"
    assert params[1] == "Prefers Python"
    assert params[2] == "prefers python"

    fake.rows = [("abc123",)]
    duplicate = await repo.add_fact(fact)
    assert duplicate is False


@pytest.mark.asyncio
async def test_open_passes_connect_timeout(monkeypatch) -> None:
    captured: dict = {}

    async def connect(conninfo: str, **kwargs):
        captured.update(kwargs)
        return _FakeConnection()

    monkeypatch.setattr(repository_module.psycopg.AsyncConnection, "connect", connect)
    repo = MemoryRepository("postgresql://x:y@localhost/db")

    await repo._open()

    assert captured.get("connect_timeout") == repository_module._CONNECT_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_get_fact_maps_row_to_fact(monkeypatch) -> None:
    now = utcnow()
    row = (
        "abc",
        "Likes Go",
        "preference",
        '["go", "systems"]',
        0.9,
        "user",
        "thread-1",
        "conversation",
        now,
        now,
        now,
        3,
        now,
        1,
        1,
    )
    fake = _FakeConnection(rows=[row])
    repo = _repository(fake, monkeypatch=monkeypatch)

    fact = await repo.get_fact("abc")

    assert fact.id == "abc"
    assert fact.content == "Likes Go"
    assert fact.category == "preference"
    assert fact.topics == ("go", "systems")
    assert fact.access_count == 3
    assert fact.revision == 1


@pytest.mark.asyncio
async def test_search_builds_and_matching_pattern(monkeypatch) -> None:
    fake = _FakeConnection(rows=[])
    repo = _repository(fake, monkeypatch=monkeypatch)

    await repo.search_facts("rust python", category="preference", limit=5)

    sql, params = fake.cursors[0].executed[0]
    assert sql.count("strpos") == 2
    assert params[:2] == ["rust", "python"]
    assert "category = %s" in sql
    assert params[-1] == 5


@pytest.mark.asyncio
async def test_update_fact_round_trip(monkeypatch) -> None:
    now = utcnow()
    row = (
        "abc",
        "Old content",
        "context",
        "[]",
        0.7,
        "user",
        "thread-1",
        "conversation",
        now,
        now,
        None,
        0,
        None,
        0,
        1,
    )
    fake = _FakeConnection(rows=[row])
    repo = _repository(fake, monkeypatch=monkeypatch)

    updated = await repo.update_fact(
        "abc",
        content="New content",
        category="decision",
        confidence=0.8,
    )
    assert updated is True
    _, params = fake.cursors[-1].executed[-1]
    assert params == ("New content", "new content", "decision", 0.8, "[]", "abc")


@pytest.mark.asyncio
async def test_profile_upsert_uses_on_conflict(monkeypatch) -> None:
    fake = _FakeConnection()
    repo = _repository(fake, monkeypatch=monkeypatch)
    profile = MemoryProfile(
        user_id="alice",
        work_context="Backend",
        personal_context="Tea drinker",
        top_of_mind="",
    )

    await repo.upsert_profile(profile)

    sql, params = fake.cursors[0].executed[0]
    assert "ON CONFLICT (user_id)" in sql
    assert params[:2] == ("alice", "Backend")


@pytest.mark.asyncio
async def test_delete_fact_reflects_existence(monkeypatch) -> None:
    fake = _FakeConnection()
    repo = _repository(fake, monkeypatch=monkeypatch)

    assert await repo.delete_fact("missing") is False

    fake.deleted.add("present")
    assert await repo.delete_fact("present") is True
