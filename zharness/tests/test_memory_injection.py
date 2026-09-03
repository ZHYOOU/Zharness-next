"""Tests for memory context formatting. / 记忆上下文格式化的测试。"""

from __future__ import annotations

from zharness.memory.injection import format_memory_injection
from zharness.memory.types import Fact, MemoryProfile, utcnow


def _fact(content: str) -> Fact:
    now = utcnow()
    return Fact(
        id=f"fact-{content[:4]}",
        content=content,
        confidence=0.9,
        created_at=now,
        updated_at=now,
    )


def test_format_returns_none_when_empty() -> None:
    assert format_memory_injection(None, []) is None


def test_format_renders_profile_and_facts() -> None:
    profile = MemoryProfile(
        user_id="default",
        work_context="Builds agents in Python.",
        personal_context="Likes Chinese food.",
        top_of_mind="Shipping the memory feature.",
    )
    block = format_memory_injection(profile, [_fact("Prefers TypeScript")])

    assert "<memory>" in block
    assert "Work: Builds agents in Python." in block
    assert "Personal: Likes Chinese food." in block
    assert "Focus: Shipping the memory feature." in block
    assert "- Prefers TypeScript" in block


def test_format_omits_empty_profile_sections() -> None:
    profile = MemoryProfile(user_id="default", work_context="Only work.")
    block = format_memory_injection(profile, [])
    assert "Personal:" not in block
    assert "Focus:" not in block


def test_format_truncates_trailing_facts_before_profile() -> None:
    profile = MemoryProfile(
        user_id="default",
        work_context="The profile summary must survive.",
    )
    facts = [
        _fact(f"fact number {index} with enough padding to matter here")
        for index in range(20)
    ]
    block = format_memory_injection(profile, facts, max_chars=300)

    assert "The profile summary must survive." in block
    assert len(block) <= 320


def test_format_truncates_final_block_when_overflow_remains() -> None:
    profile = MemoryProfile(
        user_id="default",
        work_context="A " + "very long " * 200 + "profile.",
    )
    block = format_memory_injection(profile, [], max_chars=200)
    assert block is not None
    assert len(block) <= 204
