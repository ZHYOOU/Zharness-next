from typing import cast

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langgraph.runtime import Runtime


def test_short_conversation_is_not_summarized() -> None:
    model = FakeMessagesListChatModel(responses=[AIMessage(content="unused")])
    middleware = SummarizationMiddleware(
        model=model,
        trigger=("messages", 4),
        keep=("messages", 2),
    )

    result = middleware.before_model(
        {"messages": [HumanMessage(content="hello")]},
        cast(Runtime, None),
    )

    assert result is None


def test_long_conversation_is_summarized_and_keeps_recent_messages() -> None:
    model = FakeMessagesListChatModel(
        responses=[AIMessage(content="Earlier decisions and artifacts.")]
    )
    middleware = SummarizationMiddleware(
        model=model,
        trigger=("messages", 4),
        keep=("messages", 2),
        trim_tokens_to_summarize=None,
    )
    recent_user = HumanMessage(content="recent question")
    recent_ai = AIMessage(content="recent answer")

    result = middleware.before_model(
        {
            "messages": [
                HumanMessage(content="old question one"),
                AIMessage(content="old answer one"),
                HumanMessage(content="old question two"),
                recent_user,
                recent_ai,
            ]
        },
        cast(Runtime, None),
    )

    assert result is not None
    messages = result["messages"]
    assert isinstance(messages[0], RemoveMessage)
    assert "Earlier decisions and artifacts." in str(messages[1].content)
    assert messages[-2:] == [recent_user, recent_ai]
