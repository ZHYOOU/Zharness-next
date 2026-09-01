"""Agent tool for command execution inside a thread-scoped sandbox. / 在线程作用域沙箱内执行命令的 Agent 工具。"""

from langchain.tools import ToolRuntime, tool

from zharness.host.paths import WorkspacePathError
from zharness.sandbox.manager import SandboxUnavailableError, get_sandbox_manager

MAX_COMMAND_CHARS = 128 * 1024
MAX_TIMEOUT_SECONDS = 300


@tool
def execute_command(
    command: str,
    timeout: int = 30,
    *,
    runtime: ToolRuntime,
) -> str:
    """Execute a shell command in the current thread's isolated workspace. / 在当前线程的隔离工作区中执行 shell 命令。"""

    execution_info = runtime.execution_info
    thread_id = execution_info.thread_id if execution_info is not None else None
    if thread_id is None:
        return "Error: Server thread identity is unavailable"
    if not command or len(command) > MAX_COMMAND_CHARS:
        return f"Error: command must contain 1-{MAX_COMMAND_CHARS} characters"
    if isinstance(timeout, bool) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        return f"Error: timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds"

    try:
        result = (
            get_sandbox_manager()
            .for_thread(thread_id)
            .execute(command, timeout=timeout)
        )
    except (SandboxUnavailableError, WorkspacePathError) as exc:
        return f"Error: {exc}"

    output = result.output or "(no output)"
    suffix = f"\n[exit_code={result.exit_code}]"
    if result.truncated:
        suffix += " [output truncated]"
    return output + suffix
