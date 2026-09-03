"""Deterministic write gate and content normalization for memories. / 记忆的确定性写入闸门与内容归一化。

Mirrors the DeerFlow write gate: only facts classified as user-scoped, durable,
and descriptive survive automatic extraction; everything else stays in the
conversation state.

参照 DeerFlow 的写入闸门：只有被分类为用户作用域、持久、描述性的事实才会保留
在自动抽取中；其余内容留在会话状态里。
"""

from __future__ import annotations

import re

from zharness.memory.types import FactAuthority, FactDurability, FactScope

_CLASSIFICATION_FIELDS = ("scope", "durability", "authority")
_WHITESPACE = re.compile(r"\s+")


def collapse_whitespace(content: str) -> str:
    """Collapse runs of whitespace and strip the content, preserving case.

    将连续空白折叠为单个空格并去除首尾空白，保留原始大小写。
    """
    return _WHITESPACE.sub(" ", content.strip())


def normalize_content(content: str) -> str:
    """Normalize fact content for duplicate detection. / 归一化事实内容以进行去重检测。"""
    return collapse_whitespace(content).casefold()


def fact_gate_reason(classification: dict[str, str | None]) -> str | None:
    """Return why a classification is rejected by the write gate, or ``None`` when accepted.

    返回分类被写入闸门拒绝的原因；当分类通过时返回 ``None``。
    """
    for field in _CLASSIFICATION_FIELDS:
        if _normalize_label(classification.get(field)) is None:
            return "missing"
    if _normalize_label(classification["scope"]) != FactScope.USER.value:
        return "scope"
    if _normalize_label(classification["durability"]) != FactDurability.DURABLE.value:
        return "durability"
    if _normalize_label(classification["authority"]) != FactAuthority.DESCRIPTIVE.value:
        return "authority"
    return None


def _normalize_label(value: str | None) -> str | None:
    """Normalize a classification label to a trimmed lowercase string. / 将分类标签归一化为去除空白的小写字符串。"""
    if value is None:
        return None
    label = str(value).strip().lower()
    return label or None
