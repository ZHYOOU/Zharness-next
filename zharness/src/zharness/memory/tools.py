"""Long-term memory tools exposed to the agent. / 面向 agent 开放的长期记忆工具。

Tools resolve the thread from the runtime and route through the process-wide
memory service. Storage failures surface as machine-readable error JSON rather
than raised exceptions, so the agent loop is never broken by the database.

工具从运行时解析线程，并通过进程级记忆服务路由。存储失败以机器可读的错误 JSON
呈现而非抛出异常，从而保证数据库问题不会破坏智能体循环。
"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime, tool

from zharness.memory.service import MemoryUnavailableError, get_memory_service


@tool
async def memory_search(
    query: str,
    category: str | None = None,
    limit: int | None = None,
    *,
    runtime: ToolRuntime,
) -> str:
    """Search long-term memory for durable facts about the user.

    Use it to recall preferences, constraints, decisions, goals, and past
    corrections that may apply to the current request.
    """
    try:
        result = await get_memory_service().search(
            query, category=category, limit=limit
        )
    except (MemoryUnavailableError, RuntimeError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return _dumps({"results": result["results"], "count": result["count"]})


@tool
async def memory_add(
    content: str,
    category: str = "context",
    confidence: float = 0.7,
    *,
    runtime: ToolRuntime,
) -> str:
    """Store a durable fact about the user in long-term memory.

    Use it when the user states a preference, identity detail, constraint,
    decision, or goal that should be remembered across sessions.
    """
    try:
        result = await get_memory_service().add_fact(
            content,
            category=category,
            confidence=confidence,
            thread_id=_thread_id(runtime),
            source_type="tool",
        )
    except (MemoryUnavailableError, RuntimeError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return _dumps(result)


@tool
async def memory_update(
    fact_id: str,
    content: str | None = None,
    category: str | None = None,
    confidence: float | None = None,
    *,
    runtime: ToolRuntime,
) -> str:
    """Update an existing long-term memory fact by its id. / 按 id 更新一条既有长期记忆事实。"""
    try:
        result = await get_memory_service().update_fact(
            fact_id,
            content=content,
            category=category,
            confidence=confidence,
        )
    except (MemoryUnavailableError, RuntimeError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return _dumps(result)


@tool
async def memory_delete(
    fact_id: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """Delete a long-term memory fact by its id. / 按 id 删除一条长期记忆事实。"""
    try:
        result = await get_memory_service().delete_fact(fact_id)
    except (MemoryUnavailableError, RuntimeError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    return _dumps(result)


def _thread_id(runtime: ToolRuntime) -> str | None:
    """Extract the current thread id from the runtime, if present. / 从运行时提取当前线程 id（如有）。"""
    info = runtime.execution_info
    return info.thread_id if info is not None else None


def _dumps(value: dict[str, Any]) -> str:
    """Serialize a tool result as compact JSON. / 将工具结果序列化为紧凑 JSON。"""
    return json.dumps(value, ensure_ascii=False)
