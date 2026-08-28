from langchain.tools import ToolRuntime, tool

from zharness.workspace.listing import list_workspace_entries
from zharness.workspace.paths import WorkspacePathError, ensure_thread_workspace
from zharness.workspace.reader import WorkspaceReadError, read_workspace_file
from zharness.workspace.writer import WorkspaceWriteError, write_workspace_file


def _runtime_workspace(runtime: ToolRuntime) -> str:
    execution_info = runtime.execution_info
    thread_id = execution_info.thread_id if execution_info is not None else None

    if thread_id is None:
        raise WorkspacePathError("Server thread identity is unavailable")

    return str(ensure_thread_workspace(thread_id))


@tool
def list_workspace(
    runtime: ToolRuntime,
) -> list[str] | str:
    """List files and directories in the current workspace."""

    try:
        return list_workspace_entries(_runtime_workspace(runtime))
    except (OSError, WorkspacePathError) as exc:
        return f"Error: {exc}"


@tool
def read_file(
    path: str,
    runtime: ToolRuntime,
) -> str:
    """Read a UTF-8 text file from the current workspace."""
    try:
        return read_workspace_file(
            _runtime_workspace(runtime),
            path,
        )
    except (WorkspacePathError, WorkspaceReadError) as exc:
        return f"Error: {exc}"


@tool
def write_file(
    path: str,
    content: str,
    runtime: ToolRuntime,
) -> str:
    """Create or overwrite a UTF-8 text file in the current workspace."""

    try:
        return write_workspace_file(
            _runtime_workspace(runtime),
            path,
            content,
        )
    except (WorkspacePathError, WorkspaceWriteError) as exc:
        return f"Error: {exc}"
