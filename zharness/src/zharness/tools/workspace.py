from langchain.tools import ToolRuntime, tool

from zharness.agents.context import AgentContext
from zharness.workspace.listing import list_workspace_entries


@tool
def list_workspace(
    runtime: ToolRuntime[AgentContext],
) -> list[str]:
    """List files and directories in the current workspace."""

    return list_workspace_entries(
        runtime.context.workspace_path,
    )
