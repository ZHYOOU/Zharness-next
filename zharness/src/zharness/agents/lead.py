from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware, TodoListMiddleware
from langchain_core.language_models import BaseChatModel

from zharness.tools.execute import execute_command
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
rooted at /; never assume they are host filesystem paths. Shell commands execute
inside an isolated container whose /workspace directory is this virtual root.
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
            execute_command,
        ],
        middleware=[
            TodoListMiddleware(),
            SummarizationMiddleware(
                model=model,
                trigger=("tokens", 4000),
                keep=("messages", 8),
            ),
        ],
        system_prompt=SYSTEM_PROMPT,
    )
