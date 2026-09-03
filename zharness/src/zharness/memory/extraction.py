"""LLM-driven memory extraction from conversation tails. / 基于 LLM 的会话尾部记忆抽取。

The extraction model returns a strict JSON object with candidate facts (each
classified by scope/durability/authority), contradiction removals, and an
optional user-profile update. The prompt re-lists the current memory state so
the model does not re-add known facts. Parsing is tolerant: any model error
yields an empty result instead of failing the agent turn.

抽取模型返回一个严格的 JSON 对象，包含候选事实（每个都标注作用域/持久性/权威性）、
矛盾移除与可选的用户画像更新。提示词会重新列出当前记忆状态，避免模型重复添加已知
事实。解析采用宽容策略：任何模型错误都会返回空结果，而不是使 agent 回合失败。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, SystemMessage
from pydantic import BaseModel, Field

from zharness.memory.types import Fact, MemoryProfile

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """You are the long-term memory extractor for ZHarness, a personal AI coding assistant. You inspect the tail of a user conversation and decide what should be remembered across sessions.

Extract durable, user-relevant facts that will be useful later: preferences, identity, constraints, decisions, goals, recurring behavior, and corrections of your behavior. Ignore one-off task details, thread- or project-specific state, credentials, and transient content.

For every candidate fact assign three classification labels:
- scope: "user" (about the user, their preferences/identity) | "thread" (about a single conversation) | "project" (about a specific codebase)
- durability: "durable" (stable, should persist) | "temporary" (short-lived)
- authority: "descriptive" (a stable description) | "transactional" (a one-time action or permission)

Also:
- Do NOT re-add facts that already appear in Current Memory State.
- Set confidence to a 0.0-1.0 estimate of how certain you are.
- Use category from: preference, correction, context, goal, behavior, identity, constraint, decision, other.
- Propose removals only for facts in Current Memory State that are now directly contradicted or obsolete; give a short reason.
- Update the profile summaries only when the conversation reveals new information; otherwise leave each field null to signal no change.

Return ONLY valid JSON with this exact shape:
{"facts": [{"content": "...", "category": "...", "scope": "user", "durability": "durable", "authority": "descriptive", "confidence": 0.9}], "removals": [{"fact_id": "...", "reason": "..."}], "profile": {"work_context": null, "personal_context": null, "top_of_mind": null}}
"""


class ExtractedFact(BaseModel):
    """A candidate fact extracted from a conversation. / 从会话中抽取的候选事实。"""

    content: str
    category: str = Field(default="context")
    scope: str = Field(default="user")
    durability: str = Field(default="durable")
    authority: str = Field(default="descriptive")
    confidence: float = Field(default=0.7)


class MemoryRemoval(BaseModel):
    """A proposed removal of an existing fact. / 对既有事实的移除建议。"""

    fact_id: str
    reason: str


class ProfileUpdate(BaseModel):
    """Optional user-profile summary updates. / 可选的用户画像摘要更新。"""

    work_context: str | None = None
    personal_context: str | None = None
    top_of_mind: str | None = None


class ExtractionResult(BaseModel):
    """Parsed output of one memory extraction call. / 单次记忆抽取调用的解析结果。"""

    facts: list[ExtractedFact] = Field(default_factory=list)
    removals: list[MemoryRemoval] = Field(default_factory=list)
    profile: ProfileUpdate | None = None


def build_extraction_prompt(
    existing_facts: list[Fact],
    profile: MemoryProfile | None,
) -> str:
    """Render the extraction prompt with the current memory state. / 渲染包含当前记忆状态的抽取提示词。"""
    state: list[str] = [_EXTRACTION_PROMPT, "\nCurrent Memory State:"]
    if profile is not None and (
        profile.work_context or profile.personal_context or profile.top_of_mind
    ):
        context: list[str] = []
        if profile.work_context:
            context.append(f"Work: {profile.work_context}")
        if profile.personal_context:
            context.append(f"Personal: {profile.personal_context}")
        if profile.top_of_mind:
            context.append(f"Focus: {profile.top_of_mind}")
        state.append("\n".join(context))
    if existing_facts:
        state.append(
            "\n".join(f"- [{fact.category}] {fact.content}" for fact in existing_facts)
        )
    else:
        state.append("- (none)")
    return "\n".join(state)


async def extract_memories(
    model: BaseChatModel,
    messages: list[AnyMessage],
    existing_facts: list[Fact],
    profile: MemoryProfile | None,
) -> ExtractionResult:
    """Extract candidate memories from a conversation tail. / 从会话尾部抽取候选记忆。"""
    if not messages:
        return ExtractionResult()
    prompt = SystemMessage(content=build_extraction_prompt(existing_facts, profile))
    try:
        response = await model.ainvoke([prompt, *messages])
    except Exception:
        logger.exception("Memory extraction model call failed")
        return ExtractionResult()
    return parse_extraction_response(response.content)


def parse_extraction_response(content: Any) -> ExtractionResult:
    """Parse a raw model response into an :class:`ExtractionResult`.

    将模型原始响应解析为 :class:`ExtractionResult`。
    """
    payload = _coerce_content(content)
    if not payload:
        return ExtractionResult()
    data = _extract_json(payload)
    if data is None:
        logger.warning("Memory extraction returned non-JSON content")
        return ExtractionResult()
    try:
        return ExtractionResult.model_validate(data)
    except Exception:
        logger.exception("Memory extraction JSON failed validation")
        return ExtractionResult()


def _coerce_content(content: Any) -> str | None:
    """Flatten a model content blob into a string. / 将模型内容对象展平为字符串。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif (
                isinstance(item, dict)
                and isinstance(item.get("text"), str)
                and item.get("type") in ("text", "text_delta")
            ):
                parts.append(item["text"])
        return "".join(parts)
    return None


def _extract_json(text: str) -> dict[str, Any] | None:
    """Locate and parse the first balanced JSON object in a string. / 定位并解析字符串中的首个平衡 JSON 对象。"""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None
