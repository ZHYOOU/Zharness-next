from langchain.tools import ToolRuntime, tool

from zharness.host.paths import WorkspacePathError
from zharness.sandbox.manager import SandboxUnavailableError, get_sandbox_manager
from zharness.sandbox.protocol import FileInfo, GrepMatch
from zharness.sandbox.workspace import SandboxWorkspace, SandboxWorkspaceError


def _runtime_workspace(runtime: ToolRuntime) -> SandboxWorkspace:
    execution_info = runtime.execution_info
    thread_id = execution_info.thread_id if execution_info is not None else None

    if thread_id is None:
        raise WorkspacePathError("Server thread identity is unavailable")

    return SandboxWorkspace(get_sandbox_manager().for_thread(thread_id))


_WORKSPACE_ERRORS = (
    SandboxWorkspaceError,
    SandboxUnavailableError,
    WorkspacePathError,
)


@tool
def list_workspace(
    path: str = "/workspace",
    *,
    runtime: ToolRuntime,
) -> list[FileInfo] | str:
    """List direct children and metadata at a virtual workspace path. / 列出虚拟工作区路径下的直接子项及其元数据。"""

    try:
        return _runtime_workspace(runtime).ls(path)
    except _WORKSPACE_ERRORS as exc:
        return f"Error: {exc}"


@tool
def read_file(
    path: str,
    offset: int = 0,
    limit: int = 2000,
    *,
    runtime: ToolRuntime,
) -> str:
    """Read a UTF-8 file from a virtual path with optional line pagination. / 从虚拟路径读取 UTF-8 文件，可选按行分页。"""

    try:
        return _runtime_workspace(runtime).read(path, offset=offset, limit=limit)
    except (*_WORKSPACE_ERRORS, ValueError) as exc:
        return f"Error: {exc}"


@tool
def write_file(
    path: str,
    content: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """Atomically create or overwrite a UTF-8 file at a virtual path. / 在虚拟路径上原子地创建或覆盖 UTF-8 文件。"""

    try:
        written_path = _runtime_workspace(runtime).write(path, content)
        return f"Wrote {len(content.encode('utf-8'))} bytes to {written_path}"
    except _WORKSPACE_ERRORS as exc:
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
    """Replace exact text in a UTF-8 workspace file. / 替换 UTF-8 工作区文件中的精确文本。"""

    try:
        workspace = _runtime_workspace(runtime)
        count = workspace.edit(
            path,
            old_string,
            new_string,
            replace_all=replace_all,
        )
        return f"Replaced {count} occurrence(s) in {workspace.canonical_path(path)}"
    except _WORKSPACE_ERRORS as exc:
        return f"Error: {exc}"


@tool
def delete_path(
    path: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """Delete a file or directory tree from the current workspace. / 从当前工作区删除文件或目录树。"""

    try:
        deleted_path = _runtime_workspace(runtime).delete(path)
        return f"Deleted {deleted_path}"
    except _WORKSPACE_ERRORS as exc:
        return f"Error: {exc}"


@tool
def glob_files(
    pattern: str,
    path: str = "/workspace",
    *,
    runtime: ToolRuntime,
) -> list[str] | str:
    """Find workspace files and directories matching a glob pattern. / 查找匹配 glob 模式的工作区文件和目录。"""

    try:
        return _runtime_workspace(runtime).glob(pattern, path=path)
    except _WORKSPACE_ERRORS as exc:
        return f"Error: {exc}"


@tool
def grep_files(
    pattern: str,
    path: str = "/workspace",
    include: str | None = None,
    *,
    runtime: ToolRuntime,
) -> list[GrepMatch] | str:
    """Search workspace text files for a literal string. / 在工作区文本文件中搜索字面字符串。"""

    try:
        return _runtime_workspace(runtime).grep(pattern, path=path, include=include)
    except _WORKSPACE_ERRORS as exc:
        return f"Error: {exc}"
