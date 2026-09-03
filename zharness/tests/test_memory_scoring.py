"""Tests for the deterministic eviction scoring. / 确定性驱逐评分的测试。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from zharness.memory.scoring import (
    access_heat,
    confirmation_freshness,
    hybrid_score,
    select_for_eviction,
)
from zharness.memory.types import Fact, utcnow


def _fact(**overrides) -> Fact:
    defaults = {
        "id": "fact-1",
        "content": "sample fact",
        "category": "context",
        "confidence": 0.7,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    defaults.update(overrides)
    return Fact(**defaults)


def test_confirmation_freshness_decays_over_time() -> None:
    now = utcnow()
    fresh = _fact(created_at=now, last_confirmed_at=now)
    stale = _fact(
        created_at=now - timedelta(days=90),
        last_confirmed_at=now - timedelta(days=90),
    )
    assert confirmation_freshness(fresh, now) > confirmation_freshness(stale, now)


def test_unconfirmed_facts_use_half_strength() -> None:
    now = utcnow()
    confirmed = _fact(created_at=now, last_confirmed_at=now)
    unconfirmed = _fact(created_at=now, last_confirmed_at=None)
    assert confirmation_freshness(unconfirmed, now) == pytest.approx(
        0.5 * confirmation_freshness(confirmed, now)
    )


def test_access_heat_zero_without_accesses() -> None:
    assert access_heat(_fact(access_count=0), utcnow()) == 0.0


def test_access_heat_rises_with_accesses_and_decays_with_age() -> None:
    now = utcnow()
    active = _fact(
        access_count=50,
        last_accessed_at=now - timedelta(days=1),
    )
    idle = _fact(
        access_count=50,
        last_accessed_at=now - timedelta(days=365),
    )
    assert access_heat(active, now) > access_heat(idle, now)
    assert 0.0 < access_heat(active, now) <= 1.0


def test_hybrid_score_weights_confidence() -> None:
    now = utcnow()
    high = _fact(confidence=0.9, created_at=now, updated_at=now)
    low = _fact(confidence=0.3, created_at=now, updated_at=now)
    assert hybrid_score(high, now) > hybrid_score(low, now)


def test_select_for_eviction_picks_lowest_scores() -> None:
    now = utcnow()
    facts = [
        _fact(id=f"high-{i}", confidence=0.9 - i * 0.01, created_at=now, updated_at=now)
        for i in range(5)
    ]
    facts.append(_fact(id="very-low", confidence=0.1, created_at=now, updated_at=now))

    selected = select_for_eviction(facts, max_facts=5, now=now)

    assert len(selected) == 1
    assert selected[0][1].id == "very-low"


def test_select_for_eviction_respects_capacity() -> None:
    now = utcnow()
    facts = [
        _fact(id=f"fact-{i}", confidence=0.5, created_at=now, updated_at=now)
        for i in range(10)
    ]
    selected = select_for_eviction(facts, max_facts=6, now=now)
    assert len(selected) == 4
    assert len(facts) - len(selected) == 6


def test_select_for_eviction_protects_corrections() -> None:
    now = utcnow()
    correction = _fact(
        id="corr-low",
        confidence=0.1,
        category="correction",
        created_at=now,
        updated_at=now,
    )
    context_facts = [
        _fact(
            id=f"ctx-{i}",
            confidence=0.5,
            category="context",
            created_at=now,
            updated_at=now,
        )
        for i in range(5)
    ]
    facts = [correction, *context_facts]

    selected = select_for_eviction(facts, max_facts=4, now=now)

    evicted_ids = {fact.id for _, fact in selected}
    assert len(selected) == 2
    assert "corr-low" not in evicted_ids
    assert {"ctx-0", "ctx-1"} <= evicted_ids


def test_select_for_eviction_empty_when_within_capacity() -> None:
    facts = [_fact(id=f"fact-{i}") for i in range(3)]
    assert select_for_eviction(facts, max_facts=5) == []
