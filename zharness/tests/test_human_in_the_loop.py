from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from zharness.agents.lead import (
    APPROVAL_STRATEGY_KEY,
    APPROVAL_STRATEGY_REQUIRE_APPROVAL,
    create_lead_agent,
)
from zharness.sandbox.protocol import ExecuteResponse
from zharness.tools import execute as execute_module


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        _ = tools, tool_choice, kwargs
        return self


def _model_requesting_execution() -> ToolCallingFakeModel:
    return ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute_command",
                        "args": {"command": "printf approved", "timeout": 10},
                        "id": "call-execute",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Finished."),
        ]
    )


def test_execute_requires_approval_before_sandbox_access(monkeypatch) -> None:
    executions: list[tuple[str, int]] = []
    sandbox = SimpleNamespace(
        execute=lambda command, timeout, cwd: (
            executions.append((command, timeout))
            or ExecuteResponse(output="approved", exit_code=0)
        )
    )
    manager = SimpleNamespace(for_thread=lambda thread_id: sandbox)
    monkeypatch.setattr(execute_module, "get_sandbox_manager", lambda: manager)
    agent = create_lead_agent(
        _model_requesting_execution(),
        checkpointer=InMemorySaver(),
    )
    config = {
        "configurable": {
            "thread_id": "approval-thread",
            APPROVAL_STRATEGY_KEY: APPROVAL_STRATEGY_REQUIRE_APPROVAL,
        }
    }

    interrupted = agent.invoke(
        {"messages": [{"role": "user", "content": "Run the command."}]},
        config,
    )

    assert executions == []
    request = interrupted["__interrupt__"][0].value
    assert request["action_requests"] == [
        {
            "name": "execute_command",
            "args": {"command": "printf approved", "timeout": 10},
            "description": (
                "Tool execution requires approval\n\n"
                "Tool: execute_command\n"
                "Args: {'command': 'printf approved', 'timeout': 10}"
            ),
        }
    ]
    assert request["review_configs"] == [
        {
            "action_name": "execute_command",
            "allowed_decisions": ["approve", "reject"],
        }
    ]

    resumed = agent.invoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config,
    )

    assert executions == [("printf approved", 10)]
    tool_messages = [
        message for message in resumed["messages"] if isinstance(message, ToolMessage)
    ]
    assert tool_messages[-1].content == "approved\n[exit_code=0]"


def test_rejected_execute_never_accesses_sandbox(monkeypatch) -> None:
    sandbox_accesses: list[str] = []
    manager = SimpleNamespace(
        for_thread=lambda thread_id: sandbox_accesses.append(thread_id)
    )
    monkeypatch.setattr(execute_module, "get_sandbox_manager", lambda: manager)
    agent = create_lead_agent(
        _model_requesting_execution(),
        checkpointer=InMemorySaver(),
    )
    config = {
        "configurable": {
            "thread_id": "rejection-thread",
            APPROVAL_STRATEGY_KEY: APPROVAL_STRATEGY_REQUIRE_APPROVAL,
        }
    }
    agent.invoke(
        {"messages": [{"role": "user", "content": "Run the command."}]},
        config,
    )

    resumed = agent.invoke(
        Command(
            resume={
                "decisions": [{"type": "reject", "message": "Command is not approved"}]
            }
        ),
        config,
    )

    assert sandbox_accesses == []
    rejected = [
        message
        for message in resumed["messages"]
        if isinstance(message, ToolMessage) and message.name == "execute_command"
    ]
    assert rejected[-1].status == "error"
    assert "Command is not approved" in str(rejected[-1].content)


def test_execute_is_allowed_without_approval_by_default(monkeypatch) -> None:
    executions: list[tuple[str, int]] = []
    sandbox = SimpleNamespace(
        execute=lambda command, timeout, cwd: (
            executions.append((command, timeout))
            or ExecuteResponse(output="allowed", exit_code=0)
        )
    )
    manager = SimpleNamespace(for_thread=lambda thread_id: sandbox)
    monkeypatch.setattr(execute_module, "get_sandbox_manager", lambda: manager)
    agent = create_lead_agent(
        _model_requesting_execution(),
        checkpointer=InMemorySaver(),
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Run the command."}]},
        {"configurable": {"thread_id": "allow-all-thread"}},
    )

    assert "__interrupt__" not in result
    assert executions == [("printf approved", 10)]
    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert tool_messages[-1].content == "allowed\n[exit_code=0]"
