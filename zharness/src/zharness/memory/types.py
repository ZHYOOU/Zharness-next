"""Long-term memory data types. / 长期记忆数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class FactCategory(StrEnum):
    """Enumeration of supported memory fact categories. / 支持的记忆事实分类枚举。"""

    PREFERENCE = "preference"
    CORRECTION = "correction"
    CONTEXT = "context"
    GOAL = "goal"
    BEHAVIOR = "behavior"
    IDENTITY = "identity"
    CONSTRAINT = "constraint"
    DECISION = "decision"
    OTHER = "other"


CATEGORIES = frozenset(category.value for category in FactCategory)
"""Supported fact category values. / 支持的事实分类取值。"""


class FactScope(StrEnum):
    """Classification scope of an extracted fact. / 提取事实的分类作用域。"""

    USER = "user"
    THREAD = "thread"
    PROJECT = "project"


class FactDurability(StrEnum):
    """Classification durability of an extracted fact. / 提取事实的分类持久性。"""

    DURABLE = "durable"
    TEMPORARY = "temporary"


class FactAuthority(StrEnum):
    """Classification authority of an extracted fact. / 提取事实的分类权威性。"""

    DESCRIPTIVE = "descriptive"
    TRANSACTIONAL = "transactional"


@dataclass(frozen=True, slots=True)
class Fact:
    """An atomic long-term memory record. / 一条原子化的长期记忆记录。"""

    id: str
    content: str
    category: str = FactCategory.CONTEXT.value
    topics: tuple[str, ...] = ()
    confidence: float = 0.7
    scope: str = FactScope.USER.value
    thread_id: str | None = None
    source_type: str = "conversation"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_accessed_at: datetime | None = None
    access_count: int = 0
    last_confirmed_at: datetime | None = None
    confirmation_count: int = 0
    revision: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize the fact to a plain dictionary. / 将事实序列化为普通字典。"""
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "topics": list(self.topics),
            "confidence": self.confidence,
            "scope": self.scope,
            "thread_id": self.thread_id,
            "source_type": self.source_type,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
            "last_accessed_at": _iso(self.last_accessed_at),
            "access_count": self.access_count,
            "last_confirmed_at": _iso(self.last_confirmed_at),
            "confirmation_count": self.confirmation_count,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class MemoryProfile:
    """Cross-session user profile summaries. / 跨会话的用户画像摘要。"""

    user_id: str
    work_context: str = ""
    personal_context: str = ""
    top_of_mind: str = ""
    updated_at: datetime | None = None


def utcnow() -> datetime:
    """Return the current UTC time. / 返回当前 UTC 时间。"""
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    """Format a datetime as ISO-8601, or ``None`` when absent. / 将时间格式化为 ISO-8601；为空时返回 ``None``。"""
    return value.isoformat() if value is not None else None
