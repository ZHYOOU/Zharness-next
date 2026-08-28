from langchain.tools import ToolRuntime, tool

from zharness.workspace.filesystem import (
    FileInfo,
    GrepMatch,
    WorkspaceFilesystem,
    WorkspaceFilesystemError,
)
from zharness.workspace.paths import WorkspacePathError, ensure_thread_workspace


def _runtime_filesystem(runtime: ToolRuntime) -> WorkspaceFilesystem:
    execution_info = runtime.execution_info
    thread_id = execution_info.thread_id if execution_info is not None else None

    if thread_id is None:
        raise WorkspacePathError("Server thread identity is unavailable")

    return WorkspaceFilesystem(ensure_thread_workspace(thread_id))


@tool
def list_workspace(
    path: str = "/",
    *,
    runtime: ToolRuntime,
) -> list[FileInfo] | str:
    """List direct children and metadata at a virtual workspace path."""

    try:
        return _runtime_filesystem(runtime).ls(path)
    except (WorkspaceFilesystemError, WorkspacePathError) as exc:
        return f"Error: {exc}"


@tool
def read_file(
    path: str,
    offset: int = 0,
    limit: int = 2000,
    *,
    runtime: ToolRuntime,
) -> str:
    """Read a UTF-8 file from a virtual path with optional line pagination."""

    try:
        return _runtime_filesystem(runtime).read(path, offset=offset, limit=limit)
    except (WorkspaceFilesystemError, WorkspacePathError, ValueError) as exc:
        return f"Error: {exc}"


@tool
def write_file(
    path: str,
    content: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """Atomically create or overwrite a UTF-8 file at a virtual path."""

    try:
        written_path = _runtime_filesystem(runtime).write(path, content)
        return f"Wrote {len(content.encode('utf-8'))} bytes to {written_path}"
    except (WorkspaceFilesystemError, WorkspacePathError) as exc:
        return f"Error: {exc}"


@tool
def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    *,
    runtime: ToolRuntime,
) -> str:
    """Replace exact text in a UTF-8 workspace file."""

    try:
        count = _runtime_filesystem(runtime).edit(
            path,
            old_string,
            new_string,
            replace_all=replace_all,
        )
        return f"Replaced {count} occurrence(s) in {path}"
    except (WorkspaceFilesystemError, WorkspacePathError) as exc:
        return f"Error: {exc}"


@tool
def delete_path(
    path: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """Delete a file or directory tree from the current workspace."""

    try:
        deleted_path = _runtime_filesystem(runtime).delete(path)
        return f"Deleted {deleted_path}"
    except (WorkspaceFilesystemError, WorkspacePathError) as exc:
        return f"Error: {exc}"


@tool
def glob_files(
    pattern: str,
    path: str = "/",
    *,
    runtime: ToolRuntime,
) -> list[str] | str:
    """Find workspace files and directories matching a glob pattern."""

    try:
        return _runtime_filesystem(runtime).glob(pattern, path=path)
    except (WorkspaceFilesystemError, WorkspacePathError) as exc:
        return f"Error: {exc}"


@tool
def grep_files(
    pattern: str,
    path: str = "/",
    include: str | None = None,
    *,
    runtime: ToolRuntime,
) -> list[GrepMatch] | str:
    """Search workspace text files for a literal string."""

    try:
        return _runtime_filesystem(runtime).grep(pattern, path=path, include=include)
    except (WorkspaceFilesystemError, WorkspacePathError) as exc:
        return f"Error: {exc}"
