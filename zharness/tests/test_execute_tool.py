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
    assert set(execute_command.args) == {"command", "timeout"}


def test_execute_command_uses_thread_sandbox(monkeypatch) -> None:
    sandbox = SimpleNamespace(
        execute=lambda command, timeout: ExecuteResponse(
            output="hello", exit_code=0, truncated=False
        )
    )
    manager = SimpleNamespace(for_thread=lambda thread_id: sandbox)
    monkeypatch.setattr(execute_module, "get_sandbox_manager", lambda: manager)

    result = execute_command.func("echo hello", timeout=10, runtime=runtime_for("t1"))

    assert result == "hello\n[exit_code=0]"


def test_execute_command_validates_runtime_and_timeout() -> None:
    assert execute_command.func("pwd", runtime=runtime_for(None)) == (
        "Error: Server thread identity is unavailable"
    )
    assert execute_command.func("pwd", timeout=0, runtime=runtime_for("t1")) == (
        "Error: timeout must be between 1 and 300 seconds"
    )
