"""Agent tools for thread-scoped knowledge. / 面向 Agent 的线程级知识工具。"""

from __future__ import annotations

import json
from typing import Any

from langchain.tools import ToolRuntime, tool

from zharness.knowledge.service import (
    KnowledgeUnavailableError,
    get_knowledge_service,
)


@tool
async def knowledge_search(
    query: str,
    limit: int | None = None,
    *,
    runtime: ToolRuntime,
) -> str:
    """Search reference material indexed in the current conversation.

    搜索当前会话中已建立索引的参考资料。
    """
    try:
        result = await get_knowledge_service().search(
            _thread_id(runtime),
            query,
            limit=limit,
        )
    except (KnowledgeUnavailableError, RuntimeError, ValueError) as exc:
        return _error(exc)
    return _dumps(result)


@tool
async def knowledge_ingest(
    paths: list[str],
    replace: bool = False,
    *,
    runtime: ToolRuntime,
) -> str:
    """Index UTF-8 files from the current conversation workspace.

    为当前会话工作区中的 UTF-8 文件建立索引。
    """
    try:
        result = await get_knowledge_service().ingest(
            _thread_id(runtime),
            paths,
            replace=replace,
        )
    except (KnowledgeUnavailableError, RuntimeError, ValueError) as exc:
        return _error(exc)
    return _dumps(result)


@tool
async def knowledge_list(*, runtime: ToolRuntime) -> str:
    """List knowledge documents indexed in the current conversation.

    列出当前会话中已建立索引的知识文档。
    """
    try:
        result = await get_knowledge_service().list_documents(_thread_id(runtime))
    except (KnowledgeUnavailableError, RuntimeError, ValueError) as exc:
        return _error(exc)
    return _dumps(result)


@tool
async def knowledge_delete(
    document_id: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """Delete one knowledge document from the current conversation.

    从当前会话中删除一份知识文档。
    """
    try:
        result = await get_knowledge_service().delete_document(
            _thread_id(runtime),
            document_id,
        )
    except (KnowledgeUnavailableError, RuntimeError, ValueError) as exc:
        return _error(exc)
    return _dumps(result)


def _thread_id(runtime: ToolRuntime) -> str:
    """Require a server-provided thread identity. / 要求使用服务端提供的线程身份。"""
    info = runtime.execution_info
    thread_id = info.thread_id if info is not None else None
    if not thread_id:
        raise ValueError("server thread identity is unavailable")
    return str(thread_id)


def _error(exc: Exception) -> str:
    """Serialize an operational tool error. / 序列化工具运行错误。"""
    return _dumps({"error": str(exc)})


def _dumps(value: dict[str, Any]) -> str:
    """Serialize a result as compact Unicode JSON. / 将结果序列化为紧凑的 Unicode JSON。"""
    return json.dumps(value, ensure_ascii=False)
