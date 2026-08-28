from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from zharness.tools.calculator import add

SYSTEM_PROMPT = """
You are a concise assistant.
Use available tools when calculation is required.
""".strip()


def create_lead_agent(model: BaseChatModel):
    return create_agent(
        name="lead_agent",
        model=model,
        tools=[add],
        system_prompt=SYSTEM_PROMPT,
    )
