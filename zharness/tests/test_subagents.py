from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from zharness.middleware import SubAgentMiddleware


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        _ = tools, tool_choice, kwargs
        return self


def _task_call(subagent_type: str = "researcher") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {
                    "description": "Inspect the parser and report its behavior.",
                    "subagent_type": subagent_type,
                },
                "id": "call-task",
                "type": "tool_call",
            }
        ],
    )


def _tool_runtime(
    messages: list[Any] | None = None,
    *,
    tool_call_id: str = "call-task",
) -> ToolRuntime:
    return ToolRuntime(
        state={"messages": messages or []},
        context=None,
        config={},
        stream_writer=lambda value: None,
        tool_call_id=tool_call_id,
        store=None,
    )


def test_subagent_handoff_returns_final_report() -> None:
    received_states: list[dict[str, Any]] = []

    def run_subagent(state):
        received_states.append(dict(state))
        return {"messages": [*state["messages"], AIMessage(content="Parser report")]}

    middleware = SubAgentMiddleware(
        subagents=[
            {
                "name": "researcher",
                "description": "Inspects source code.",
                "runnable": RunnableLambda(run_subagent),
            }
        ]
    )
    model = ToolCallingFakeModel(
        responses=[_task_call(), AIMessage(content="Integrated report")]
    )
    agent = create_agent(model, tools=[], middleware=[middleware])

    result = agent.invoke({"messages": [HumanMessage(content="Review the parser.")]})

    assert [message.content for message in received_states[0]["messages"]] == [
        "Inspect the parser and report its behavior."
    ]
    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert tool_messages[-1].content == "Parser report"
    assert result["messages"][-1].content == "Integrated report"


def test_forked_subagent_inherits_parent_conversation() -> None:
    received_messages: list[Any] = []

    def run_subagent(state):
        received_messages.extend(state["messages"])
        return {"messages": [*state["messages"], AIMessage(content="Fork report")]}

    middleware = SubAgentMiddleware(
        subagents=[
            {
                "name": "researcher",
                "description": "Inspects source code.",
                "runnable": RunnableLambda(run_subagent),
                "mode": "fork",
            }
        ]
    )
    model = ToolCallingFakeModel(
        responses=[_task_call(), AIMessage(content="Integrated report")]
    )
    agent = create_agent(model, tools=[], middleware=[middleware])

    agent.invoke({"messages": [HumanMessage(content="Review the parser.")]})

    assert [message.content for message in received_messages] == [
        "Review the parser.",
        "Inspect the parser and report its behavior.",
    ]


def test_unknown_subagent_type_becomes_tool_error_for_model() -> None:
    middleware = SubAgentMiddleware(
        subagents=[
            {
                "name": "researcher",
                "description": "Inspects source code.",
                "runnable": RunnableLambda(
                    lambda state: {"messages": [AIMessage(content="unused")]}
                ),
            }
        ]
    )
    model = ToolCallingFakeModel(
        responses=[_task_call("missing"), AIMessage(content="Recovered")]
    )
    agent = create_agent(model, tools=[], middleware=[middleware])

    result = agent.invoke({"messages": [HumanMessage(content="Review it.")]})

    tool_message = next(
        message for message in result["messages"] if isinstance(message, ToolMessage)
    )
    assert "Allowed types: `researcher`" in tool_message.content


@pytest.mark.asyncio
async def test_subagent_async_invocation() -> None:
    async def run_subagent(state):
        return {"messages": [AIMessage(content="Async report")]}

    middleware = SubAgentMiddleware(
        subagents=[
            {
                "name": "researcher",
                "description": "Inspects source code.",
                "runnable": RunnableLambda(run_subagent),
            }
        ]
    )
    runtime = _tool_runtime([HumanMessage(content="Review the parser.")])
    result = await middleware.tools[0].coroutine(
        description="Inspect the parser and report its behavior.",
        subagent_type="researcher",
        runtime=runtime,
    )

    tool_message = result.update["messages"][0]
    assert tool_message.content == "Async report"


def test_subagent_cannot_delegate_to_another_subagent() -> None:
    nested_invocations: list[str] = []
    nested_middleware = SubAgentMiddleware(
        subagents=[
            {
                "name": "nested",
                "description": "Must never run.",
                "runnable": RunnableLambda(
                    lambda state: (
                        nested_invocations.append("ran")
                        or {"messages": [AIMessage(content="Nested report")]}
                    )
                ),
            }
        ]
    )

    def run_outer_subagent(state):
        refusal = nested_middleware.tools[0].func(
            description="Delegate again.",
            subagent_type="nested",
            runtime=_tool_runtime(state["messages"], tool_call_id="nested-call"),
        )
        return {"messages": [AIMessage(content=refusal)]}

    outer_middleware = SubAgentMiddleware(
        subagents=[
            {
                "name": "outer",
                "description": "Attempts nested delegation.",
                "runnable": RunnableLambda(run_outer_subagent),
            }
        ]
    )

    result = outer_middleware.tools[0].func(
        description="Run the outer task.",
        subagent_type="outer",
        runtime=_tool_runtime(),
    )

    assert nested_invocations == []
    assert "cannot delegate to another subagent" in result.update["messages"][0].content


@pytest.mark.asyncio
async def test_subagent_cannot_delegate_asynchronously() -> None:
    nested_invocations: list[str] = []

    async def run_nested(state):
        nested_invocations.append("ran")
        return {"messages": [AIMessage(content="Nested report")]}

    nested_middleware = SubAgentMiddleware(
        subagents=[
            {
                "name": "nested",
                "description": "Must never run.",
                "runnable": RunnableLambda(run_nested),
            }
        ]
    )

    async def run_outer(state):
        refusal = await nested_middleware.tools[0].coroutine(
            description="Delegate again.",
            subagent_type="nested",
            runtime=_tool_runtime(state["messages"], tool_call_id="nested-call"),
        )
        return {"messages": [AIMessage(content=refusal)]}

    outer_middleware = SubAgentMiddleware(
        subagents=[
            {
                "name": "outer",
                "description": "Attempts nested delegation.",
                "runnable": RunnableLambda(run_outer),
            }
        ]
    )

    result = await outer_middleware.tools[0].coroutine(
        description="Run the outer task.",
        subagent_type="outer",
        runtime=_tool_runtime(),
    )

    assert nested_invocations == []
    assert "cannot delegate to another subagent" in result.update["messages"][0].content


def test_subagent_configuration_validation() -> None:
    runnable = RunnableLambda(lambda state: {"messages": [AIMessage(content="result")]})
    with pytest.raises(ValueError, match="At least one"):
        SubAgentMiddleware(subagents=[])
    with pytest.raises(ValueError, match="Duplicate subagent name"):
        SubAgentMiddleware(
            subagents=[
                {"name": "same", "description": "First.", "runnable": runnable},
                {"name": "same", "description": "Second.", "runnable": runnable},
            ]
        )
    with pytest.raises(ValueError, match="must specify 'model'"):
        SubAgentMiddleware(
            subagents=[{"name": "raw", "description": "Raw agent.", "tools": []}]
        )
