from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from zharness.agents.context import AgentContext
from zharness.tools.calculator import add
from zharness.tools.workspace import list_workspace, read_file

SYSTEM_PROMPT = """
You are a concise assistant.
Use available tools when calculation is required.
""".strip()


# noinspection PyTypeChecker
def create_lead_agent(model: BaseChatModel):
    return create_agent(
        name="lead_agent",
        model=model,
        tools=[add, list_workspace, read_file],
        system_prompt=SYSTEM_PROMPT,
        context_schema=AgentContext,
    )
