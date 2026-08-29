from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models import BaseChatModel

from zharness.tools.workspace import (
    delete_path,
    edit_file,
    glob_files,
    grep_files,
    list_workspace,
    read_file,
    write_file,
)

SYSTEM_PROMPT = """
You are a concise assistant.
Use workspace tools to inspect and modify files. Workspace paths are virtual and
rooted at /; never assume they are host filesystem paths.
""".strip()


# noinspection PyTypeChecker
def create_lead_agent(model: BaseChatModel):
    return create_agent(
        name="lead_agent",
        model=model,
        tools=[
            list_workspace,
            read_file,
            write_file,
            edit_file,
            delete_path,
            glob_files,
            grep_files,
        ],
        middleware=[TodoListMiddleware()],
        system_prompt=SYSTEM_PROMPT,
    )
