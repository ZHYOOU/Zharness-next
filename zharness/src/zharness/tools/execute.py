"""Agent tool for command execution inside a thread-scoped sandbox. / 在线程作用域沙箱内执行命令的 Agent 工具。"""

from langchain.tools import ToolRuntime, tool

from zharness.host.paths import WorkspacePathError
from zharness.sandbox.manager import SandboxUnavailableError, get_sandbox_manager
from zharness.sandbox.workspace import SandboxWorkspace, SandboxWorkspaceError

MAX_COMMAND_CHARS = 128 * 1024
MAX_TIMEOUT_SECONDS = 300


@tool
def execute_command(
    command: str,
    timeout: int = 30,
    cwd: str = "/workspace",
    *,
    runtime: ToolRuntime,
) -> str:
    """Execute a shell command from a virtual workspace directory.

    `cwd` uses the same public paths as workspace file tools: `/workspace` is
    the workspace root and `/workspace/reports` is its reports directory.
    It must be an absolute path under `/workspace`. Backend host paths are
    resolved internally and must not be passed by callers.

    从工作区目录执行 shell 命令。`cwd` 使用与工作区文件工具相同的公开路径：
    `/workspace` 是工作区根目录，`/workspace/reports` 是其中的 reports 目录。
    它必须是 `/workspace` 之下的绝对路径。后端宿主机路径由内部解析，调用方不得传入。
    """

    execution_info = runtime.execution_info
    thread_id = execution_info.thread_id if execution_info is not None else None
    if thread_id is None:
        return "Error: Server thread identity is unavailable"
    if not command or len(command) > MAX_COMMAND_CHARS:
        return f"Error: command must contain 1-{MAX_COMMAND_CHARS} characters"
    if isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        return f"Error: timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds"
    try:
        backend_cwd = SandboxWorkspace.command_cwd(cwd)
        result = (
            get_sandbox_manager()
            .for_thread(thread_id)
            .execute(
                command,
                timeout=timeout,
                cwd=backend_cwd,
            )
        )
    except (
        SandboxUnavailableError,
        SandboxWorkspaceError,
        WorkspacePathError,
    ) as exc:
        return f"Error: {exc}"

    output = result.output or "(no output)"
    suffix = f"\n[exit_code={result.exit_code}]"
    if result.truncated:
        suffix += " [output truncated]"
    return output + suffix
