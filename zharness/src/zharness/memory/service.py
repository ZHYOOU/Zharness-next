"""Long-term memory service orchestration. / 长期记忆服务的编排层。

The service applies the deterministic write gate, deduplication, capacity
eviction, and profile updates on top of the repository, and exposes the
operations used by the memory middleware and tools. It degrades fail-open on
storage errors so an unavailable database never breaks the agent loop.

服务在仓储之上应用确定性写入闸门、去重、容量驱逐与画像更新，并暴露记忆中间件
和工具使用的操作。存储出错时采取开放失败策略，数据库不可用也不会破坏智能体循环。
"""

from __future__ import annotations

import logging
from typing import Any

from zharness.memory.extraction import ExtractionResult, ProfileUpdate
from zharness.memory.gate import (
    collapse_whitespace,
    fact_gate_reason,
)
from zharness.memory.repository import MemoryRepository
from zharness.memory.scoring import select_for_eviction
from zharness.memory.types import (
    CATEGORIES,
    Fact,
    FactCategory,
    MemoryProfile,
    utcnow,
)

logger = logging.getLogger(__name__)


class MemoryUnavailableError(RuntimeError):
    """Raised when long-term memory storage cannot be reached. / 长期记忆存储不可达时抛出。"""


class MemoryService:
    """Apply policy and orchestrate persistence for long-term memory.

    为长期记忆应用策略并编排持久化。
    """

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        user_id: str = "default",
        max_facts: int = 200,
        min_confidence: float = 0.7,
        search_limit: int = 10,
        gate_enabled: bool = True,
    ) -> None:
        """Initialize the service with policy settings. / 使用策略配置初始化服务。"""
        self._repository = repository
        self._user_id = user_id
        self._max_facts = max_facts
        self._min_confidence = min_confidence
        self._search_limit = search_limit
        self._gate_enabled = gate_enabled
        self._ready = False

    @property
    def user_id(self) -> str:
        """Return the memory owner identity. / 返回记忆所属的用户身份。"""
        return self._user_id

    async def _ensure_ready(self) -> None:
        """Create tables on first use, failing open on storage errors. / 首次使用时建表；存储出错时开放失败。"""
        if self._ready:
            return
        try:
            await self._repository.setup()
            self._ready = True
        except Exception as exc:
            raise MemoryUnavailableError(str(exc)) from exc

    async def add_fact(
        self,
        content: str,
        category: str = FactCategory.CONTEXT.value,
        confidence: float = 0.7,
        *,
        thread_id: str | None = None,
        source_type: str = "manual",
    ) -> dict[str, Any]:
        """Add a fact, deduplicating by normalized content and enforcing capacity.

        添加一条事实：按归一化内容去重并强制执行容量限制。
        """
        await self._ensure_ready()
        content = collapse_whitespace(content)
        if not content:
            return {"error": "Fact content must not be empty"}
        category = _coerce_category(category)
        confidence = _clamp_confidence(confidence)
        now = utcnow()
        fact = Fact(
            id=_new_fact_id(),
            content=content,
            category=category,
            confidence=confidence,
            scope="user",
            thread_id=thread_id,
            source_type=source_type,
            created_at=now,
            updated_at=now,
        )
        try:
            added = await self._repository.add_fact(fact)
        except Exception as exc:
            logger.exception("Failed to store memory fact")
            raise MemoryUnavailableError(str(exc)) from exc
        if not added:
            return {"error": "Duplicate fact"}
        await self._enforce_capacity()
        return {"id": fact.id, "status": "added"}

    async def update_fact(
        self,
        fact_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Partially update a fact. / 部分更新一条事实。"""
        await self._ensure_ready()
        if content is not None and not content.strip():
            return {"error": "Fact content must not be empty"}
        if category is not None:
            category = _coerce_category(category)
        if confidence is not None:
            confidence = _clamp_confidence(confidence)
        try:
            updated = await self._repository.update_fact(
                fact_id,
                content=content,
                category=category,
                confidence=confidence,
            )
        except Exception as exc:
            logger.exception("Failed to update memory fact")
            raise MemoryUnavailableError(str(exc)) from exc
        if not updated:
            return {"error": "Fact not found"}
        return {"id": fact_id, "status": "updated"}

    async def delete_fact(self, fact_id: str) -> dict[str, Any]:
        """Delete a fact. / 删除一条事实。"""
        await self._ensure_ready()
        try:
            deleted = await self._repository.delete_fact(fact_id)
        except Exception as exc:
            logger.exception("Failed to delete memory fact")
            raise MemoryUnavailableError(str(exc)) from exc
        if not deleted:
            return {"error": "Fact not found"}
        return {"id": fact_id, "status": "deleted"}

    async def search(
        self,
        query: str,
        category: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Search facts and record access heat on the results.

        搜索事实并在结果上记录访问热度。
        """
        await self._ensure_ready()
        limit = limit or self._search_limit
        query = query.strip()
        if not query:
            return {"results": [], "count": 0}
        try:
            facts = await self._repository.search_facts(query, category, limit)
            for fact in facts:
                await self._repository.record_access(fact.id)
        except Exception as exc:
            logger.exception("Failed to search memory facts")
            raise MemoryUnavailableError(str(exc)) from exc
        return {"results": [fact.to_dict() for fact in facts], "count": len(facts)}

    async def top_facts(
        self,
        limit: int,
        min_confidence: float | None = None,
    ) -> list[Fact]:
        """Return the highest-confidence facts for context injection.

        返回用于上下文注入的置信度最高的事实。
        """
        await self._ensure_ready()
        threshold = (
            min_confidence if min_confidence is not None else self._min_confidence
        )
        try:
            facts = await self._repository.all_facts()
        except Exception as exc:
            logger.exception("Failed to read memory facts")
            raise MemoryUnavailableError(str(exc)) from exc
        ranked = sorted(
            (fact for fact in facts if fact.confidence >= threshold),
            key=lambda fact: (fact.confidence, fact.updated_at or utcnow()),
            reverse=True,
        )
        return ranked[:limit]

    async def apply_extraction(
        self,
        result: ExtractionResult,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply an extraction result through the gate, dedup, and capacity policy.

        通过闸门、去重与容量策略应用一次抽取结果。
        """
        await self._ensure_ready()
        metrics: dict[str, Any] = {
            "added": 0,
            "duplicate": 0,
            "rejected": 0,
            "removed": 0,
            "rejections": {},
        }
        for fact in result.facts:
            outcome = await self._store_candidate(fact, thread_id)
            status = outcome["status"]
            metrics[status] += 1
            if status == "rejected" and outcome.get("reason"):
                rejections = metrics["rejections"]
                rejections[outcome["reason"]] = rejections.get(outcome["reason"], 0) + 1
        for removal in result.removals:
            deleted = await self._repository.delete_fact(removal.fact_id)
            if deleted:
                metrics["removed"] += 1
        if result.profile is not None:
            await self._merge_profile(result.profile)
        return metrics

    async def get_profile(self) -> MemoryProfile | None:
        """Return the stored user profile, or ``None`` when absent.

        返回已存储的用户画像；不存在时返回 ``None``。
        """
        await self._ensure_ready()
        try:
            return await self._repository.get_profile(self._user_id)
        except Exception as exc:
            logger.exception("Failed to read memory profile")
            raise MemoryUnavailableError(str(exc)) from exc

    async def close(self) -> None:
        """Close the service; currently a no-op. / 关闭服务；当前为空操作。"""
        await self._repository.close()

    async def _store_candidate(
        self,
        candidate: Any,
        thread_id: str | None,
    ) -> dict[str, Any]:
        """Gate, deduplicate, and store a single extracted candidate.

        对单个抽取候选执行闸门、去重与入库。
        """
        classification = {
            "scope": candidate.scope,
            "durability": candidate.durability,
            "authority": candidate.authority,
        }
        if self._gate_enabled:
            reason = fact_gate_reason(classification)
            if reason is not None:
                return {"status": "rejected", "reason": reason}
        if candidate.confidence < self._min_confidence:
            return {"status": "rejected", "reason": "confidence"}
        outcome = await self.add_fact(
            candidate.content,
            category=candidate.category,
            confidence=candidate.confidence,
            thread_id=thread_id,
            source_type="conversation",
        )
        if outcome.get("error") == "Duplicate fact":
            return {"status": "duplicate"}
        if "error" in outcome:
            return {"status": "rejected", "reason": outcome["error"]}
        return {"status": "added"}

    async def _merge_profile(self, update: ProfileUpdate) -> None:
        """Merge non-empty profile fields into the stored profile. / 将非空画像字段合并入已存储画像。"""
        current = await self.get_profile()
        existing = current or MemoryProfile(user_id=self._user_id)
        merged = MemoryProfile(
            user_id=self._user_id,
            work_context=(
                update.work_context if update.work_context else existing.work_context
            ),
            personal_context=(
                update.personal_context
                if update.personal_context
                else existing.personal_context
            ),
            top_of_mind=(
                update.top_of_mind if update.top_of_mind else existing.top_of_mind
            ),
        )
        try:
            await self._repository.upsert_profile(merged)
        except Exception as exc:
            logger.exception("Failed to persist memory profile")
            raise MemoryUnavailableError(str(exc)) from exc

    async def _enforce_capacity(self) -> None:
        """Evict the lowest-scoring facts when the collection exceeds ``max_facts``.

        当集合超出 ``max_facts`` 时驱逐评分最低的事实。
        """
        try:
            count = await self._repository.count_facts()
            if count <= self._max_facts:
                return
            facts = await self._repository.all_facts()
            for score, fact in select_for_eviction(facts, self._max_facts):
                await self._repository.delete_fact(fact.id)
                await self._repository.record_eviction(fact, score, "capacity")
            await self._repository.prune_evictions()
        except Exception:
            logger.exception("Failed to enforce memory capacity")


_MEMORY_SERVICE: MemoryService | None = None


def get_memory_service() -> MemoryService:
    """Return the process-wide memory service, creating it on first use.

    返回进程级记忆服务，首次使用时创建。
    """
    global _MEMORY_SERVICE
    if _MEMORY_SERVICE is None:
        from zharness.config import get_settings
        from zharness.server.database import postgres_uri

        settings = get_settings().memory
        _MEMORY_SERVICE = MemoryService(
            MemoryRepository(postgres_uri()),
            user_id=settings.user_id,
            max_facts=settings.max_facts,
            min_confidence=settings.min_confidence,
            search_limit=settings.search_limit,
            gate_enabled=settings.gate_enabled,
        )
    return _MEMORY_SERVICE


def _coerce_category(category: str) -> str:
    """Return a supported category, falling back to ``other``. / 返回受支持的分类，否则回退为 ``other``。"""
    value = str(category).strip().lower()
    return value if value in CATEGORIES else FactCategory.OTHER.value


def _clamp_confidence(value: float) -> float:
    """Clamp a confidence value into the ``0.0``-``1.0`` range. / 将置信度限制在 ``0.0``-``1.0`` 区间内。"""
    return max(0.0, min(1.0, float(value)))


def _new_fact_id() -> str:
    """Generate a new fact id. / 生成新的事实 id。"""
    import uuid

    return uuid.uuid4().hex
