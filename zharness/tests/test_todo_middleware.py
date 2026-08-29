from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from zharness.agents.lead import create_lead_agent


class ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        _ = tools, tool_choice, kwargs
        return self


def test_write_todos_updates_agent_state() -> None:
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_todos",
                        "args": {
                            "todos": [
                                {
                                    "content": "Inspect the workspace",
                                    "status": "in_progress",
                                }
                            ]
                        },
                        "id": "call-todos",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Plan created."),
        ]
    )
    agent = create_lead_agent(model)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "Plan this task."}]}
    )

    assert result["todos"] == [
        {
            "content": "Inspect the workspace",
            "status": "in_progress",
        }
    ]
