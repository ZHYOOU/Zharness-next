"""Tests for token usage collection. / Token 用量收集测试。"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.message import add_messages
from zharness.middleware.token_usage import (
    SUBAGENT_TOKEN_USAGE_ATTRIBUTED_KEY,
    SUBAGENT_TOKEN_USAGE_KEY,
    TokenUsageMiddleware,
    accumulate_token_usage,
    normalize_token_usage,
)


def test_normalize_token_usage_and_derive_missing_total() -> None:
    assert normalize_token_usage({"input_tokens": 10, "output_tokens": 4}) == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
    }
    assert (
        normalize_token_usage(
            {"input_tokens": -1, "output_tokens": 4, "total_tokens": 3}
        )
        is None
    )


def test_accumulate_token_usage_deduplicates_message_ids() -> None:
    first = AIMessage(
        id="first",
        content="one",
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    )
    duplicate = first.model_copy(update={"content": "updated"})
    second = AIMessage(
        content="two",
        usage_metadata={"input_tokens": 20, "output_tokens": 3, "total_tokens": 23},
    )

    assert accumulate_token_usage(
        [HumanMessage(content="hello"), first, duplicate, second]
    ) == {"input_tokens": 30, "output_tokens": 5, "total_tokens": 35}


def test_middleware_logs_provider_usage(caplog) -> None:
    message = AIMessage(
        content="done",
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )

    with caplog.at_level("INFO", logger="zharness.middleware.token_usage"):
        result = TokenUsageMiddleware().after_model(
            {"messages": [message]}, MagicMock()
        )

    assert result is None
    assert "LLM token usage: input=100 output=20 total=120" in caplog.text


def test_middleware_logs_provider_usage_details(caplog) -> None:
    """Preserve cache and reasoning details in logs. / 在日志中保留缓存与推理明细。"""
    message = AIMessage(
        content="done",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_token_details": {"cache_read": 80},
            "output_token_details": {"reasoning": 10},
        },
    )

    with caplog.at_level("INFO", logger="zharness.middleware.token_usage"):
        TokenUsageMiddleware().after_model({"messages": [message]}, MagicMock())

    assert "input_token_details={'cache_read': 80}" in caplog.text
    assert "output_token_details={'reasoning': 10}" in caplog.text


def test_middleware_attributes_subagent_usage_once() -> None:
    dispatch = AIMessage(
        id="dispatch",
        content="",
        tool_calls=[{"id": "task-1", "name": "task", "args": {}}],
        usage_metadata={"input_tokens": 5, "output_tokens": 1, "total_tokens": 6},
    )
    tool_result = ToolMessage(
        id="result",
        content="report",
        tool_call_id="task-1",
        additional_kwargs={
            SUBAGENT_TOKEN_USAGE_KEY: {
                "input_tokens": 30,
                "output_tokens": 7,
                "total_tokens": 37,
            }
        },
    )
    final = AIMessage(content="done")
    messages = [dispatch, tool_result, final]
    middleware = TokenUsageMiddleware()

    first_update = middleware.after_model({"messages": messages}, MagicMock())

    assert first_update is not None
    updated_messages = add_messages(messages, first_update["messages"])
    updated_dispatch = next(
        message for message in updated_messages if message.id == "dispatch"
    )
    updated_result = next(
        message for message in updated_messages if message.id == "result"
    )
    assert updated_dispatch.usage_metadata == {
        "input_tokens": 35,
        "output_tokens": 8,
        "total_tokens": 43,
    }
    assert updated_result.additional_kwargs[SUBAGENT_TOKEN_USAGE_ATTRIBUTED_KEY] is True
    assert middleware.after_model({"messages": updated_messages}, MagicMock()) is None


def test_middleware_sums_parallel_subagent_results() -> None:
    """Accumulate every result in one delegated batch. / 累加同一委派批次的全部结果。"""
    dispatch = AIMessage(
        id="dispatch",
        content="",
        tool_calls=[
            {"id": "task-1", "name": "task", "args": {}},
            {"id": "task-2", "name": "task", "args": {}},
        ],
    )
    results = [
        ToolMessage(
            id=f"result-{index}",
            content="report",
            tool_call_id=f"task-{index}",
            additional_kwargs={
                SUBAGENT_TOKEN_USAGE_KEY: {
                    "input_tokens": 10 * index,
                    "output_tokens": index,
                    "total_tokens": 11 * index,
                }
            },
        )
        for index in (1, 2)
    ]

    update = TokenUsageMiddleware().after_model(
        {"messages": [dispatch, *results, AIMessage(content="done")]},
        MagicMock(),
    )

    assert update is not None
    updated_dispatch = next(
        message for message in update["messages"] if message.id == "dispatch"
    )
    assert updated_dispatch.usage_metadata == {
        "input_tokens": 30,
        "output_tokens": 3,
        "total_tokens": 33,
    }
