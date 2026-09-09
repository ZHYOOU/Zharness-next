import asyncio

from langchain_core.messages import ToolMessage
from zharness.middleware.tool_result import (
    TOOL_RESULT_MAX_CHARS,
    TRUNCATION_GUIDANCE,
    ToolResultTruncationMiddleware,
)


def test_truncates_sync_tool_result() -> None:
    middleware = ToolResultTruncationMiddleware()
    message = ToolMessage(content="x" * (TOOL_RESULT_MAX_CHARS + 1), tool_call_id="1")

    result = middleware.wrap_tool_call({}, lambda _: message)  # type: ignore[arg-type]

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "1"
    assert isinstance(result.content, str)
    assert result.content.endswith(TRUNCATION_GUIDANCE)
    assert len(result.content) <= TOOL_RESULT_MAX_CHARS + len(TRUNCATION_GUIDANCE) + 2


def test_keeps_small_structured_tool_result() -> None:
    middleware = ToolResultTruncationMiddleware()
    message = ToolMessage(content=[{"type": "text", "text": "ok"}], tool_call_id="1")

    result = middleware.wrap_tool_call({}, lambda _: message)  # type: ignore[arg-type]

    assert result is message


def test_truncates_async_tool_result() -> None:
    middleware = ToolResultTruncationMiddleware()
    message = ToolMessage(content="x" * (TOOL_RESULT_MAX_CHARS + 1), tool_call_id="1")

    async def handler(_):
        return message

    result = asyncio.run(middleware.awrap_tool_call({}, handler))  # type: ignore[arg-type]

    assert isinstance(result, ToolMessage)
    assert result.content.endswith(TRUNCATION_GUIDANCE)
