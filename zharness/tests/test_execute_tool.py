from types import SimpleNamespace
from typing import cast

from langchain.tools import ToolRuntime
from zharness.sandbox.protocol import ExecuteResponse
from zharness.tools import execute as execute_module
from zharness.tools.execute import execute_command


def runtime_for(thread_id: str | None) -> ToolRuntime:
    return cast(
        ToolRuntime,
        SimpleNamespace(execution_info=SimpleNamespace(thread_id=thread_id)),
    )


def test_runtime_is_hidden_from_execute_schema() -> None:
    assert set(execute_command.args) == {"command", "timeout", "cwd"}


def test_execute_command_uses_thread_sandbox(monkeypatch) -> None:
    calls: list[tuple[str, int, str]] = []

    def execute(command: str, *, timeout: int, cwd: str) -> ExecuteResponse:
        calls.append((command, timeout, cwd))
        return ExecuteResponse(output="hello", exit_code=0, truncated=False)

    sandbox = SimpleNamespace(execute=execute)
    manager = SimpleNamespace(for_thread=lambda thread_id: sandbox)
    monkeypatch.setattr(execute_module, "get_sandbox_manager", lambda: manager)

    result = execute_command.func(
        "echo hello",
        timeout=10,
        cwd="/workspace/reports/daily",
        runtime=runtime_for("t1"),
    )

    assert result == "hello\n[exit_code=0]"
    assert calls == [("echo hello", 10, "/workspace/reports/daily")]


def test_execute_command_validates_runtime_and_timeout() -> None:
    assert execute_command.func("pwd", runtime=runtime_for(None)) == (
        "Error: Server thread identity is unavailable"
    )
    assert execute_command.func("pwd", timeout=0, runtime=runtime_for("t1")) == (
        "Error: timeout must be between 1 and 300 seconds"
    )
    assert execute_command.func("pwd", cwd="../outside", runtime=runtime_for("t1")) == (
        "Error: Path traversal is not allowed"
    )


def test_execute_command_allows_shell_directory_changes(monkeypatch) -> None:
    calls: list[tuple[str, int, str]] = []

    def execute(command: str, *, timeout: int, cwd: str) -> ExecuteResponse:
        calls.append((command, timeout, cwd))
        return ExecuteResponse(output="ok", exit_code=0)

    sandbox = SimpleNamespace(execute=execute)
    manager = SimpleNamespace(for_thread=lambda thread_id: sandbox)
    monkeypatch.setattr(execute_module, "get_sandbox_manager", lambda: manager)

    result = execute_command.func(
        "cd nested && python analysis.py",
        cwd="/workspace/reports",
        runtime=runtime_for("t1"),
    )

    assert result == "ok\n[exit_code=0]"
    assert calls == [("cd nested && python analysis.py", 30, "/workspace/reports")]


def test_execute_command_rejects_cwd_outside_public_workspace(monkeypatch) -> None:
    calls: list[str] = []

    def execute(command: str, *, timeout: int, cwd: str) -> ExecuteResponse:
        calls.append(cwd)
        return ExecuteResponse(output="ok", exit_code=0)

    sandbox = SimpleNamespace(execute=execute)
    manager = SimpleNamespace(for_thread=lambda thread_id: sandbox)
    monkeypatch.setattr(execute_module, "get_sandbox_manager", lambda: manager)

    result = execute_command.func("pwd", cwd="/reports", runtime=runtime_for("t1"))

    assert result == "Error: Path must be under /workspace"
    assert calls == []
