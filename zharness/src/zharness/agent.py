import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_deepseek import ChatDeepSeek

from zharness.tools.calculator import add

load_dotenv()


def build_agent():
    """Build a Zharness agent."""
    model_name = os.environ["ZHARNESS_MODEL"]

    model = ChatDeepSeek(
        model=model_name,
        temperature=0,
        timeout=60,
        max_retries=3,
    )

    return create_agent(
        name="lead_agent",
        model=model,
        tools=[add],
        system_prompt=(
            "You are a concise assistant. "
            "Use available tools when calculation is required."
        ),
    )
