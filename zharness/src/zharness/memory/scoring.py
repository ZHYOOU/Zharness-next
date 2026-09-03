"""Deterministic fact eviction scoring. / 确定性的事实驱逐评分。

Adapted from the DeerFlow ``hybrid-v1`` policy: a weighted blend of confidence,
confirmation freshness, and access heat determines which facts are evicted when
the collection exceeds ``max_facts``. Correction facts keep a reserved capacity
share so user feedback is not silently forgotten.

改编自 DeerFlow 的 ``hybrid-v1`` 策略：由置信度、确认新鲜度与访问热度加权组合
决定集合超出 ``max_facts`` 时驱逐哪些事实。纠正类事实保留一定的容量配额，
避免用户反馈被静默遗忘。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime

from zharness.memory.types import Fact, FactCategory, utcnow

DEFAULT_WEIGHTS = (0.65, 0.25, 0.10)
CONFIRMATION_HALF_LIFE_DAYS = 90
ACCESS_HALF_LIFE_DAYS = 30
CORRECTION_RESERVED_FRACTION = 0.10
CORRECTION_RESERVED_MAX = 10


def confirmation_freshness(
    fact: Fact,
    now: datetime | None = None,
    half_life_days: int = CONFIRMATION_HALF_LIFE_DAYS,
) -> float:
    """Return the freshness of a fact's last confirmation, decaying with age.

    返回事实最近确认的新鲜度，随时间呈指数衰减。
    """
    now = now or utcnow()
    base = fact.last_confirmed_at or fact.created_at or now
    strength = 1.0 if fact.last_confirmed_at is not None else 0.5
    elapsed_days = max(0.0, (now - base).total_seconds() / 86_400.0)
    return strength * (2 ** (-elapsed_days / half_life_days))


def access_heat(
    fact: Fact,
    now: datetime | None = None,
    half_life_days: int = ACCESS_HALF_LIFE_DAYS,
) -> float:
    """Return normalized decaying access heat for a fact. / 返回事实归一化且随时间衰减的访问热度。"""
    if fact.access_count <= 0:
        return 0.0
    now = now or utcnow()
    base = fact.last_accessed_at or fact.updated_at or now
    elapsed_days = max(0.0, (now - base).total_seconds() / 86_400.0)
    heat = fact.access_count * (2 ** (-elapsed_days / half_life_days))
    return min(1.0, math.log1p(heat) / math.log(9.0))


def hybrid_score(
    fact: Fact,
    now: datetime | None = None,
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
) -> float:
    """Score a fact for eviction using confidence, freshness, and heat.

    使用置信度、新鲜度与热度对事实进行驱逐评分。
    """
    confidence_weight, freshness_weight, heat_weight = weights
    return (
        confidence_weight * fact.confidence
        + freshness_weight * confirmation_freshness(fact, now)
        + heat_weight * access_heat(fact, now)
    )


def select_for_eviction(
    facts: Iterable[Fact],
    max_facts: int,
    now: datetime | None = None,
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
    correction_fraction: float = CORRECTION_RESERVED_FRACTION,
    correction_max: int = CORRECTION_RESERVED_MAX,
) -> list[tuple[float, Fact]]:
    """Select the lowest-scoring facts to evict until the collection fits ``max_facts``.

    挑选评分最低的事实进行驱逐，直到集合不超过 ``max_facts`` 条。
    """
    facts = list(facts)
    if len(facts) <= max_facts:
        return []
    reserved = min(correction_max, math.ceil(max_facts * correction_fraction))
    scored = sorted(
        ((hybrid_score(fact, now, weights), fact) for fact in facts),
        key=lambda pair: pair[0],
    )
    selected: list[tuple[float, Fact]] = []
    protected_corrections = 0
    for score, fact in scored:
        if len(facts) - len(selected) <= max_facts:
            break
        if (
            fact.category == FactCategory.CORRECTION.value
            and protected_corrections < reserved
        ):
            protected_corrections += 1
            continue
        selected.append((score, fact))
    return selected
