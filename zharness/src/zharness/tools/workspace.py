from langchain.tools import ToolRuntime, tool

from zharness.agents.context import AgentContext
from zharness.workspace.listing import list_workspace_entries
from zharness.workspace.reader import WorkspaceReadError, read_workspace_file


@tool
def list_workspace(
    runtime: ToolRuntime[AgentContext],
) -> list[str]:
    """List files and directories in the current workspace."""

    return list_workspace_entries(
        runtime.context.workspace_path,
    )


@tool
def read_file(
    path: str,
    runtime: ToolRuntime[AgentContext],
) -> str:
    """Read a UTF-8 text file from the current workspace."""
    try:
        return read_workspace_file(
            runtime.context.workspace_path,
            path,
        )
    except WorkspaceReadError as exc:
        return f"Error: {exc}"
