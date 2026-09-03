"""Tests for memory extraction prompts and parsing. / 记忆抽取提示词与解析的测试。"""

from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from zharness.memory.extraction import (
    ExtractionResult,
    build_extraction_prompt,
    extract_memories,
    parse_extraction_response,
)
from zharness.memory.types import Fact, MemoryProfile, utcnow


def test_build_extraction_prompt_lists_existing_state() -> None:
    now = utcnow()
    profile = MemoryProfile(
        user_id="default",
        work_context="Builds agents in Python.",
    )
    fact = Fact(
        id="f1",
        content="Prefers TypeScript",
        category="preference",
        created_at=now,
        updated_at=now,
    )
    prompt = build_extraction_prompt([fact], profile)

    assert "Current Memory State" in prompt
    assert "Work: Builds agents in Python." in prompt
    assert "- [preference] Prefers TypeScript" in prompt


def test_build_extraction_prompt_marks_empty_state() -> None:
    prompt = build_extraction_prompt([], None)
    assert "- (none)" in prompt


def test_parse_valid_json_payload() -> None:
    payload = (
        '{"facts": [{"content": "Likes Python", "category": "preference", '
        '"scope": "user", "durability": "durable", "authority": "descriptive", '
        '"confidence": 0.95}], "removals": [{"fact_id": "f1", "reason": "outdated"}], '
        '"profile": {"work_context": "Python developer", "personal_context": null, '
        '"top_of_mind": null}}'
    )
    result = parse_extraction_response(payload)

    assert isinstance(result, ExtractionResult)
    assert len(result.facts) == 1
    assert result.facts[0].content == "Likes Python"
    assert result.facts[0].category == "preference"
    assert result.removals[0].fact_id == "f1"
    assert result.profile is not None
    assert result.profile.work_context == "Python developer"


def test_parse_tolerates_surrounding_text() -> None:
    payload = (
        "Here is the result:\n\n"
        '{"facts": [{"content": "ok"}], "removals": [], "profile": null}\n'
    )
    result = parse_extraction_response(payload)
    assert len(result.facts) == 1
    assert result.facts[0].content == "ok"


def test_parse_returns_empty_on_bad_payloads() -> None:
    assert parse_extraction_response("not json").facts == []
    assert parse_extraction_response("[1, 2]").facts == []
    assert parse_extraction_response('{"facts": "oops"}').facts == []
    assert parse_extraction_response("").facts == []


@pytest.mark.asyncio
async def test_extract_memories_uses_model_response() -> None:
    payload = (
        '{"facts": [{"content": "Likes Go", "category": "preference"}], '
        '"removals": [], "profile": null}'
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content=payload)])

    result = await extract_memories(
        model,
        [HumanMessage(content="I like Go.")],
        [],
        None,
    )

    assert len(result.facts) == 1
    assert result.facts[0].content == "Likes Go"


@pytest.mark.asyncio
async def test_extract_memories_returns_empty_on_model_error() -> None:
    class ExplodingModel(FakeMessagesListChatModel):
        async def ainvoke(self, *args, **kwargs):
            raise RuntimeError("model down")

    result = await extract_memories(
        ExplodingModel(responses=[AIMessage(content="")]),
        [HumanMessage(content="hello")],
        [],
        None,
    )
    assert result.facts == []
    assert result.removals == []
