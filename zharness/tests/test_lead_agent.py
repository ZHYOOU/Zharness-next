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
    assert "write_file" in agent.nodes["tools"].bound.tools_by_name
