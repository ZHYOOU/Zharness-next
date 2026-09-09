"""Limit model-facing tool results. / 限制面向模型的工具结果。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

TOOL_RESULT_TOKEN_LIMIT = 20_000
TOOL_RESULT_MAX_CHARS = TOOL_RESULT_TOKEN_LIMIT * 4
TRUNCATION_GUIDANCE = (
    "... [results truncated, try being more specific with your parameters]"
)


def _truncate_if_too_long(result: str | list[str]) -> str | list[str]:
    """Truncate text results using the shared approximate token budget.

    使用统一的近似 token 预算截断文本结果。
    """
    if isinstance(result, list):
        total_chars = sum(len(item) for item in result)
        if total_chars > TOOL_RESULT_MAX_CHARS:
            keep_count = len(result) * TOOL_RESULT_MAX_CHARS // total_chars
            return result[:keep_count] + [TRUNCATION_GUIDANCE]
        return result
    if len(result) > TOOL_RESULT_MAX_CHARS:
        return result[:TOOL_RESULT_MAX_CHARS] + "\n" + TRUNCATION_GUIDANCE
    return result


def _truncate_content(content: Any) -> Any:
    """Truncate supported tool content while preserving small structured values.

    截断支持的工具内容，同时保留较小的结构化值。
    """
    if isinstance(content, str):
        return _truncate_if_too_long(content)
    if isinstance(content, list) and all(isinstance(item, str) for item in content):
        return _truncate_if_too_long(content)

    try:
        serialized = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        serialized = str(content)
    truncated = _truncate_if_too_long(serialized)
    return truncated if truncated != serialized else content


def _truncate_message(message: ToolMessage) -> ToolMessage:
    """Return a tool message with bounded model-facing content. / 返回内容受限的工具消息。"""
    content = _truncate_content(message.content)
    if content == message.content:
        return message
    return message.model_copy(update={"content": content})


def _truncate_result(result: ToolMessage | Command[Any]) -> ToolMessage | Command[Any]:
    """Bound tool messages returned directly or inside a command. / 限制直接返回或嵌在命令中的工具消息。"""
    if isinstance(result, ToolMessage):
        return _truncate_message(result)
    update = result.update
    if not isinstance(update, dict) or not isinstance(update.get("messages"), list):
        return result
    messages = update["messages"]
    bounded_messages = [
        _truncate_message(message) if isinstance(message, ToolMessage) else message
        for message in messages
    ]
    if bounded_messages == messages:
        return result
    return Command(
        graph=result.graph,
        update={**update, "messages": bounded_messages},
        resume=result.resume,
        goto=result.goto,
    )


class ToolResultTruncationMiddleware(AgentMiddleware):
    """Apply one size limit to every model-facing tool result. / 为所有面向模型的工具结果应用统一大小限制。"""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Truncate a synchronous tool result. / 截断同步工具结果。"""
        return _truncate_result(handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Truncate an asynchronous tool result. / 截断异步工具结果。"""
        return _truncate_result(await handler(request))
