"""Tests for agent recovery middleware. / Agent 恢复中间件测试。"""

import asyncio

from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from zharness.middleware.agent_recovery import (
    DANGLING_TOOL_CALL_MESSAGE,
    EMPTY_RESPONSE_FALLBACK,
    MODEL_ERROR_RESPONSE,
    DanglingToolCallMiddleware,
    LLMErrorHandlingMiddleware,
    TerminalResponseMiddleware,
)
from zharness.middleware.subagents import create_sub_agent


def _tool_call_message(*call_ids: str) -> AIMessage:
    """Build an AI message containing test tool calls. / 构建包含测试工具调用的 AI 消息。"""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": f"tool_{call_id}",
                "args": {},
                "id": call_id,
                "type": "tool_call",
            }
            for call_id in call_ids
        ],
    )


def test_dangling_tool_calls_are_completed_before_the_next_message() -> None:
    middleware = DanglingToolCallMiddleware()
    calling = _tool_call_message("one", "two")
    existing = ToolMessage(content="done", tool_call_id="one", name="tool_one")
    following = HumanMessage(content="continue")

    update = middleware.before_model(
        {"messages": [calling, existing, following]},
        None,  # type: ignore[arg-type]
    )

    assert update is not None
    messages = update["messages"]
    assert isinstance(messages[0], RemoveMessage)
    assert messages[1:3] == [calling, existing]
    repair = messages[3]
    assert isinstance(repair, ToolMessage)
    assert repair.tool_call_id == "two"
    assert repair.name == "tool_two"
    assert repair.status == "error"
    assert repair.content == DANGLING_TOOL_CALL_MESSAGE
    assert messages[4] == following


def test_complete_tool_call_history_is_not_rewritten() -> None:
    middleware = DanglingToolCallMiddleware()
    calling = _tool_call_message("one")
    result = ToolMessage(content="done", tool_call_id="one", name="tool_one")

    assert (
        middleware.before_model(
            {"messages": [calling, result]},
            None,  # type: ignore[arg-type]
        )
        is None
    )


def test_dangling_tool_call_repair_is_persisted_by_agent_graph() -> None:
    calling = _tool_call_message("one")
    following = HumanMessage(content="continue")
    agent = create_agent(
        FakeMessagesListChatModel(responses=[AIMessage(content="Recovered")]),
        middleware=[DanglingToolCallMiddleware()],
    )

    result = agent.invoke({"messages": [calling, following]})

    messages = result["messages"]
    assert messages[0] == calling
    assert isinstance(messages[1], ToolMessage)
    assert messages[1].tool_call_id == "one"
    assert messages[2] == following
    assert messages[3].text == "Recovered"


def test_terminal_response_retries_empty_content() -> None:
    middleware = TerminalResponseMiddleware(max_retries=2)
    responses = iter(
        [
            ModelResponse(result=[AIMessage(content="")]),
            ModelResponse(result=[AIMessage(content="   ")]),
            ModelResponse(result=[AIMessage(content="Recovered")]),
        ]
    )
    attempts: list[int] = []

    def handler(request) -> ModelResponse:
        attempts.append(1)
        return next(responses)

    result = middleware.wrap_model_call(None, handler)  # type: ignore[arg-type]

    assert attempts == [1, 1, 1]
    assert result.result[-1].text == "Recovered"


def test_terminal_response_accepts_tool_calls_with_empty_text() -> None:
    middleware = TerminalResponseMiddleware(max_retries=2)
    attempts: list[int] = []

    def handler(request) -> ModelResponse:
        attempts.append(1)
        return ModelResponse(result=[_tool_call_message("one")])

    result = middleware.wrap_model_call(None, handler)  # type: ignore[arg-type]

    assert attempts == [1]
    assert isinstance(result.result[-1], AIMessage)
    assert result.result[-1].tool_calls


def test_terminal_response_returns_fallback_after_retry_limit() -> None:
    middleware = TerminalResponseMiddleware(max_retries=1)

    result = middleware.wrap_model_call(
        None,  # type: ignore[arg-type]
        lambda request: ModelResponse(result=[AIMessage(content="")]),
    )

    assert result.result[-1].text == EMPTY_RESPONSE_FALLBACK


def test_llm_error_handling_retries_then_recovers() -> None:
    middleware = LLMErrorHandlingMiddleware(max_retries=1)
    attempts: list[int] = []

    def handler(request) -> ModelResponse:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("provider details")
        return ModelResponse(result=[AIMessage(content="Recovered")])

    result = middleware.wrap_model_call(None, handler)  # type: ignore[arg-type]

    assert attempts == [1, 1]
    assert result.result[-1].text == "Recovered"


def test_llm_error_handling_logs_and_sanitizes_exhausted_error(caplog) -> None:
    middleware = LLMErrorHandlingMiddleware(max_retries=0)

    def handler(request) -> ModelResponse:
        raise RuntimeError("secret provider details")

    with caplog.at_level("ERROR", logger="zharness.middleware.agent_recovery"):
        result = middleware.wrap_model_call(None, handler)  # type: ignore[arg-type]

    assert result.result[-1].text == MODEL_ERROR_RESPONSE
    assert "secret provider details" not in result.result[-1].text
    assert "secret provider details" in caplog.text


def test_async_terminal_response_retries_empty_content() -> None:
    middleware = TerminalResponseMiddleware(max_retries=1)
    responses = iter(
        [
            ModelResponse(result=[AIMessage(content="")]),
            ModelResponse(result=[AIMessage(content="Recovered")]),
        ]
    )

    async def handler(request) -> ModelResponse:
        return next(responses)

    result = asyncio.run(middleware.awrap_model_call(None, handler))  # type: ignore[arg-type]

    assert result.result[-1].text == "Recovered"


def test_declarative_subagent_uses_terminal_response_recovery() -> None:
    model = FakeMessagesListChatModel(
        responses=[AIMessage(content=""), AIMessage(content="Recovered")]
    )
    agent = create_sub_agent(
        {
            "name": "researcher",
            "description": "Inspect source code.",
            "model": model,
            "tools": [],
        }
    )

    result = agent.invoke({"messages": [HumanMessage(content="Inspect it.")]})

    assert result["messages"][-1].text == "Recovered"
