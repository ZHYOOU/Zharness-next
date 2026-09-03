"""Render long-term memory as hidden model context. / 将长期记忆渲染为隐藏的模型上下文。

The block is appended to the model request's system message on every lead-agent
call. Overflow drops the lowest-relevance (trailing) facts first, then truncates
the remaining block, so the guaranteed profile summary survives budget pressure.

该块会在每次主 agent 调用时追加到模型请求的系统消息中。超出预算时优先丢弃最不相关
（靠后）的事实，再截断剩余块，从而保证画像摘要不受预算压力影响。
"""

from __future__ import annotations

from zharness.memory.types import Fact, MemoryProfile


def format_memory_injection(
    profile: MemoryProfile | None,
    facts: list[Fact],
    *,
    max_chars: int = 2000,
) -> str | None:
    """Build the ``<memory>`` context block, or ``None`` when empty.

    构建 ``<memory>`` 上下文块；内容为空时返回 ``None``。
    """
    profile_block = _render_profile(profile)
    facts_block = _render_facts(facts)
    block = _join_blocks(profile_block, facts_block)
    if block is None:
        return None
    if len(block) <= max_chars:
        return block

    remaining = list(facts)
    while (
        remaining
        and len(_join_blocks(profile_block, _render_facts(remaining))) > max_chars
    ):
        remaining.pop()
    block = _join_blocks(profile_block, _render_facts(remaining))
    if len(block) > max_chars:
        block = block[:max_chars].rstrip() + " …"
    return block


def _render_profile(profile: MemoryProfile | None) -> str | None:
    """Render the user profile summaries, or ``None`` when empty. / 渲染用户画像摘要；为空时返回 ``None``。"""
    if profile is None:
        return None
    lines: list[str] = []
    if profile.work_context:
        lines.append(f"Work: {profile.work_context}")
    if profile.personal_context:
        lines.append(f"Personal: {profile.personal_context}")
    if profile.top_of_mind:
        lines.append(f"Focus: {profile.top_of_mind}")
    if not lines:
        return None
    return "<user_context>\n" + "\n".join(lines) + "\n</user_context>"


def _render_facts(facts: list[Fact]) -> str | None:
    """Render the ranked fact list, or ``None`` when empty. / 渲染排序后的事实列表；为空时返回 ``None``。"""
    if not facts:
        return None
    lines = [f"- {fact.content}" for fact in facts]
    return "<facts>\n" + "\n".join(lines) + "\n</facts>"


def _join_blocks(*blocks: str | None) -> str | None:
    """Join non-empty memory blocks into a single block. / 将非空的记忆块合并为单个块。"""
    present = [block for block in blocks if block]
    if not present:
        return None
    return "<memory>\n" + "\n\n".join(present) + "\n</memory>"
