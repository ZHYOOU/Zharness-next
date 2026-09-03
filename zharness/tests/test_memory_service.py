"""Tests for the memory service policy layer. / 记忆服务策略层的测试。"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

import pytest
from zharness.memory.extraction import (
    ExtractedFact,
    ExtractionResult,
    MemoryRemoval,
    ProfileUpdate,
)
from zharness.memory.service import MemoryService, MemoryUnavailableError
from zharness.memory.types import Fact, MemoryProfile, utcnow


class FakeMemoryRepository:
    """In-memory repository implementing the service's persistence contract.

    内存仓储，实现服务所需的持久化契约。
    """

    def __init__(self) -> None:
        self.facts: dict[str, Fact] = {}
        self.profile: MemoryProfile | None = None
        self.evictions: list[tuple[Fact, float, str]] = []
        self.setup_called = 0

    async def setup(self) -> None:
        self.setup_called += 1

    async def add_fact(self, fact: Fact) -> bool:
        if fact.content.strip().casefold() in {
            stored.content.strip().casefold() for stored in self.facts.values()
        }:
            return False
        self.facts[fact.id] = fact
        return True

    async def get_fact(self, fact_id: str) -> Fact | None:
        return self.facts.get(fact_id)

    async def update_fact(
        self,
        fact_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
        topics: Sequence[str] | None = None,
    ) -> bool:
        fact = self.facts.get(fact_id)
        if fact is None:
            return False
        self.facts[fact_id] = Fact(
            id=fact.id,
            content=fact.content if content is None else content,
            category=fact.category if category is None else category,
            confidence=fact.confidence if confidence is None else confidence,
            topics=fact.topics if topics is None else tuple(topics),
            scope=fact.scope,
            thread_id=fact.thread_id,
            source_type=fact.source_type,
            created_at=fact.created_at,
            updated_at=utcnow(),
            access_count=fact.access_count,
            last_confirmed_at=fact.last_confirmed_at,
            confirmation_count=fact.confirmation_count,
            revision=fact.revision + 1,
        )
        return True

    async def delete_fact(self, fact_id: str) -> bool:
        return self.facts.pop(fact_id, None) is not None

    async def search_facts(
        self,
        query: str,
        category: str | None = None,
        limit: int = 10,
    ) -> list[Fact]:
        lowered = query.lower()
        matches = [
            fact
            for fact in self.facts.values()
            if lowered in fact.content.lower()
            and (category is None or fact.category == category)
        ]
        return matches[:limit]

    async def all_facts(self) -> list[Fact]:
        return list(self.facts.values())

    async def count_facts(self) -> int:
        return len(self.facts)

    async def record_access(self, fact_id: str) -> None:
        fact = self.facts.get(fact_id)
        if fact is not None:
            self.facts[fact_id] = dataclasses.replace(
                fact,
                access_count=fact.access_count + 1,
            )

    async def record_confirmation(self, fact_id: str) -> None:
        fact = self.facts.get(fact_id)
        if fact is not None:
            self.facts[fact_id] = dataclasses.replace(
                fact,
                confirmation_count=fact.confirmation_count + 1,
            )

    async def get_profile(self, user_id: str) -> MemoryProfile | None:
        return self.profile

    async def upsert_profile(self, profile: MemoryProfile) -> None:
        self.profile = profile

    async def record_eviction(self, fact: Fact, score: float, reason: str) -> None:
        self.evictions.append((fact, score, reason))

    async def prune_evictions(self, keep: int = 100) -> None:
        if keep:
            del self.evictions[: max(0, len(self.evictions) - keep)]
        else:
            self.evictions.clear()

    async def close(self) -> None:
        pass


def _service(repo: FakeMemoryRepository | None = None, **kwargs) -> MemoryService:
    return MemoryService(
        repo or FakeMemoryRepository(),
        user_id="alice",
        max_facts=kwargs.get("max_facts", 10),
        min_confidence=kwargs.get("min_confidence", 0.7),
        gate_enabled=kwargs.get("gate_enabled", True),
    )


@pytest.mark.asyncio
async def test_add_fact_stores_and_deduplicates() -> None:
    service = _service()
    added = await service.add_fact("Likes Python", confidence=0.9)
    assert added["status"] == "added"
    assert "id" in added

    duplicate = await service.add_fact("  likes  python  ", confidence=0.9)
    assert duplicate["error"] == "Duplicate fact"


@pytest.mark.asyncio
async def test_add_fact_rejects_empty_content() -> None:
    service = _service()
    assert (await service.add_fact("   "))["error"] == "Fact content must not be empty"


@pytest.mark.asyncio
async def test_add_fact_coerces_category_and_confidence() -> None:
    repo = FakeMemoryRepository()
    service = _service(repo)
    await service.add_fact("A fact", category="unknown-category", confidence=1.5)
    stored = next(iter(repo.facts.values()))
    assert stored.category == "other"
    assert stored.confidence == 1.0


@pytest.mark.asyncio
async def test_update_and_delete_fact() -> None:
    repo = FakeMemoryRepository()
    service = _service(repo)
    added = await service.add_fact("Original content")
    fact_id = added["id"]

    updated = await service.update_fact(fact_id, content="New content")
    assert updated["status"] == "updated"
    assert repo.facts[fact_id].content == "New content"
    assert repo.facts[fact_id].revision == 2

    deleted = await service.delete_fact(fact_id)
    assert deleted["status"] == "deleted"
    assert (await service.delete_fact(fact_id))["error"] == "Fact not found"


@pytest.mark.asyncio
async def test_search_returns_matching_facts_and_records_access() -> None:
    repo = FakeMemoryRepository()
    service = _service(repo)
    await service.add_fact("Prefers Rust for systems work", category="preference")
    await service.add_fact("Enjoys hiking on weekends", category="preference")

    result = await service.search("rust", category="preference")

    assert result["count"] == 1
    assert result["results"][0]["content"] == "Prefers Rust for systems work"
    stored = repo.facts[result["results"][0]["id"]]
    assert stored.access_count == 1


@pytest.mark.asyncio
async def test_top_facts_returns_highest_confidence_first() -> None:
    service = _service()
    await service.add_fact("low", confidence=0.5)
    await service.add_fact("high", confidence=0.95)
    await service.add_fact("mid", confidence=0.8)

    facts = await service.top_facts(limit=2)

    assert [fact.content for fact in facts] == ["high", "mid"]


@pytest.mark.asyncio
async def test_apply_extraction_applies_write_gate() -> None:
    repo = FakeMemoryRepository()
    service = _service(repo)
    result = ExtractionResult(
        facts=[
            ExtractedFact(
                content="Keeps user-scoped durable facts",
                scope="user",
                durability="durable",
                authority="descriptive",
            ),
            ExtractedFact(
                content="Thread-local detail",
                scope="thread",
                durability="durable",
                authority="descriptive",
            ),
            ExtractedFact(
                content="One-time permission granted",
                scope="user",
                durability="durable",
                authority="transactional",
            ),
            ExtractedFact(
                content="Low-confidence guess",
                scope="user",
                durability="durable",
                authority="descriptive",
                confidence=0.2,
            ),
        ]
    )

    metrics = await service.apply_extraction(result)

    assert metrics["added"] == 1
    assert metrics["rejected"] == 3
    assert metrics["rejections"] == {
        "scope": 1,
        "authority": 1,
        "confidence": 1,
    }
    assert len(repo.facts) == 1


@pytest.mark.asyncio
async def test_apply_extraction_applies_removals_and_profile() -> None:
    repo = FakeMemoryRepository()
    service = _service(repo)
    added = await service.add_fact("Old preference that is now wrong")

    result = ExtractionResult(
        facts=[],
        removals=[MemoryRemoval(fact_id=added["id"], reason="contradicted")],
        profile=ProfileUpdate(
            work_context="Python developer",
            personal_context="",
            top_of_mind="",
        ),
    )
    metrics = await service.apply_extraction(result)

    assert metrics["removed"] == 1
    assert repo.profile is not None
    assert repo.profile.work_context == "Python developer"


@pytest.mark.asyncio
async def test_profile_merge_preserves_existing_fields() -> None:
    repo = FakeMemoryRepository()
    service = _service(repo)
    await service.apply_extraction(
        ExtractionResult(
            facts=[],
            removals=[],
            profile=ProfileUpdate(work_context="Backend", personal_context="Likes tea"),
        )
    )
    await service.apply_extraction(
        ExtractionResult(
            facts=[],
            removals=[],
            profile=ProfileUpdate(work_context="Full-stack", personal_context=None),
        )
    )

    assert repo.profile.work_context == "Full-stack"
    assert repo.profile.personal_context == "Likes tea"


@pytest.mark.asyncio
async def test_add_fact_enforces_capacity() -> None:
    repo = FakeMemoryRepository()
    service = _service(repo, max_facts=3)
    for index in range(5):
        await service.add_fact(f"fact-{index}", confidence=0.5 + index * 0.05)

    assert len(repo.facts) == 3
    assert len(repo.evictions) == 2
    assert repo.evictions[0][2] == "capacity"


@pytest.mark.asyncio
async def test_service_fails_open_on_storage_errors() -> None:
    class BrokenRepository(FakeMemoryRepository):
        async def setup(self) -> None:
            raise RuntimeError("database unreachable")

    service = _service(BrokenRepository())
    with pytest.raises(MemoryUnavailableError):
        await service.add_fact("will fail")
