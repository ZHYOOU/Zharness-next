"""Recovery middleware for malformed histories and failed model responses.

用于修复异常历史和模型失败响应的恢复中间件。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRetryMiddleware
from langchain.agents.middleware.types import (
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

DANGLING_TOOL_CALL_MESSAGE = (
    "This tool call was not completed because the previous agent run ended "
    "before a result was recorded. Re-evaluate whether the tool should be called again."
)
MODEL_ERROR_RESPONSE = "The model is temporarily unavailable after multiple attempts. Please try again later."
EMPTY_RESPONSE_FALLBACK = (
    "The model returned no usable response after multiple attempts. Please try again."
)


def _repair_dangling_tool_calls(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Insert error results before a pending tool-call batch is interrupted.

    在待处理的工具调用批次被中断前插入错误结果。
    """
    repaired: list[BaseMessage] = []
    pending: dict[str, str | None] = {}

    def finish_pending() -> None:
        """Append synthetic results for all unresolved calls. / 为所有未解决调用追加合成结果。"""
        for call_id, tool_name in pending.items():
            repaired.append(
                ToolMessage(
                    content=DANGLING_TOOL_CALL_MESSAGE,
                    tool_call_id=call_id,
                    name=tool_name,
                    status="error",
                )
            )
        pending.clear()

    for message in messages:
        if isinstance(message, ToolMessage):
            repaired.append(message)
            pending.pop(message.tool_call_id, None)
            continue

        if pending:
            finish_pending()
        repaired.append(message)
        if isinstance(message, AIMessage):
            pending.update(
                {
                    call["id"]: call.get("name")
                    for call in message.tool_calls
                    if call.get("id")
                }
            )

    if pending:
        finish_pending()
    return repaired


class DanglingToolCallMiddleware(AgentMiddleware):
    """Repair AI tool calls that have no corresponding tool result.

    修复没有对应工具结果的 AI 工具调用。
    """

    def before_model(
        self, state: AgentState[Any], runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        """Normalize message history before it reaches the model. / 在消息历史到达模型前对其进行规范化。"""
        del runtime
        messages = list(state.get("messages", []))
        repaired = _repair_dangling_tool_calls(messages)
        if len(repaired) == len(messages):
            return None
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *repaired,
            ]
        }


def _retry_all_model_errors(exc: Exception) -> bool:
    """Retry every ordinary model exception. / 重试每个普通模型异常。"""
    del exc
    return True


def _format_model_error(exc: Exception) -> str:
    """Log an exhausted model error and return a safe response.

    记录重试耗尽的模型错误并返回安全响应。
    """
    logger.error(
        "Model call failed after retries",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return MODEL_ERROR_RESPONSE


class LLMErrorHandlingMiddleware(ModelRetryMiddleware):
    """Retry model failures and convert exhausted errors to a safe response.

    重试模型失败，并将重试耗尽的错误转换为安全响应。
    """

    def __init__(self, *, max_retries: int = 2) -> None:
        """Initialize bounded model retries. / 初始化有界模型重试。"""
        super().__init__(
            max_retries=max_retries,
            retry_on=_retry_all_model_errors,
            on_failure=_format_model_error,
            initial_delay=0.25,
            backoff_factor=2.0,
            max_delay=2.0,
            jitter=False,
        )


def _has_usable_terminal_response(response: ModelResponse[Any]) -> bool:
    """Return whether a model response can safely terminate the run.

    返回模型响应是否可以安全终止本次运行。
    """
    if response.structured_response is not None:
        return True
    for message in reversed(response.result):
        if not isinstance(message, AIMessage):
            continue
        if message.tool_calls:
            return True
        return bool(message.text.strip())
    return False


class TerminalResponseMiddleware(AgentMiddleware):
    """Retry empty terminal responses and provide a bounded fallback.

    重试空的终态响应，并在有界尝试后提供兜底响应。
    """

    def __init__(self, *, max_retries: int = 2) -> None:
        """Initialize the empty-response retry limit. / 初始化空响应重试上限。"""
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.max_retries = max_retries

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Retry unusable synchronous terminal responses. / 重试不可用的同步终态响应。"""
        for _ in range(self.max_retries + 1):
            response = handler(request)
            if _has_usable_terminal_response(response):
                return response
        return ModelResponse(result=[AIMessage(content=EMPTY_RESPONSE_FALLBACK)])

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Retry unusable asynchronous terminal responses. / 重试不可用的异步终态响应。"""
        for _ in range(self.max_retries + 1):
            response = await handler(request)
            if _has_usable_terminal_response(response):
                return response
        return ModelResponse(result=[AIMessage(content=EMPTY_RESPONSE_FALLBACK)])


__all__ = [
    "DANGLING_TOOL_CALL_MESSAGE",
    "EMPTY_RESPONSE_FALLBACK",
    "MODEL_ERROR_RESPONSE",
    "DanglingToolCallMiddleware",
    "LLMErrorHandlingMiddleware",
    "TerminalResponseMiddleware",
]
