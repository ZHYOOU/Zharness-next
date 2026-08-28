from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from zharness.agents.lead import create_lead_agent


def test_create_lead_agent() -> None:
    model = FakeMessagesListChatModel(responses=[AIMessage(content="hello")])

    agent = create_lead_agent(model)

    assert agent.name == "lead_agent"
    assert agent.context_schema is None
    assert set(agent.nodes["tools"].bound.tools_by_name) == {
        "add",
        "list_workspace",
        "read_file",
        "write_file",
        "edit_file",
        "delete_path",
        "glob_files",
        "grep_files",
    }
