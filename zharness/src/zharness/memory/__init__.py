"""Long-term memory subsystem for ZHarness. / ZHarness 的长期记忆子系统。

Adapted from the DeerFlow DeerMem design: facts are classified by the extraction
model, filtered by a deterministic write gate, deduplicated by normalized
content, capped by a hybrid eviction score, and surfaced to the agent as hidden
context plus the ``memory_*`` tools.

改编自 DeerFlow DeerMem 设计：抽取模型对事实分类，确定性写入闸门过滤，按归一化
内容去重，以混合驱逐评分限定容量，并以隐藏上下文和 ``memory_*`` 工具呈现给 agent。
"""

from zharness.memory.extraction import (
    ExtractedFact,
    ExtractionResult,
    MemoryRemoval,
    ProfileUpdate,
    extract_memories,
    parse_extraction_response,
)
from zharness.memory.gate import fact_gate_reason, normalize_content
from zharness.memory.injection import format_memory_injection
from zharness.memory.middleware import MemoryMiddleware
from zharness.memory.repository import MemoryRepository
from zharness.memory.scoring import (
    access_heat,
    confirmation_freshness,
    hybrid_score,
    select_for_eviction,
)
from zharness.memory.service import (
    MemoryService,
    MemoryUnavailableError,
    get_memory_service,
)
from zharness.memory.tools import (
    memory_add,
    memory_delete,
    memory_search,
    memory_update,
)
from zharness.memory.types import (
    CATEGORIES,
    Fact,
    FactAuthority,
    FactCategory,
    FactDurability,
    FactScope,
    MemoryProfile,
    utcnow,
)

__all__ = [
    "CATEGORIES",
    "ExtractedFact",
    "ExtractionResult",
    "Fact",
    "FactAuthority",
    "FactCategory",
    "FactDurability",
    "FactScope",
    "MemoryMiddleware",
    "MemoryProfile",
    "MemoryRemoval",
    "MemoryRepository",
    "MemoryService",
    "MemoryUnavailableError",
    "ProfileUpdate",
    "access_heat",
    "confirmation_freshness",
    "extract_memories",
    "fact_gate_reason",
    "format_memory_injection",
    "get_memory_service",
    "hybrid_score",
    "memory_add",
    "memory_delete",
    "memory_search",
    "memory_update",
    "normalize_content",
    "parse_extraction_response",
    "select_for_eviction",
    "utcnow",
]
