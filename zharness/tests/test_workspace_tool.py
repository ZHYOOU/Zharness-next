from pathlib import Path
from types import SimpleNamespace
from typing import cast

from langchain.tools import ToolRuntime
from zharness.tools.workspace import list_workspace, read_file


def test_read_file_only_exposes_path_to_model() -> None:
    assert set(read_file.args) == {"path"}


def test_list_workspace_hides_runtime_from_model_schema() -> None:
    assert list_workspace.args == {}


def runtime_for(thread_id: str | None) -> ToolRuntime:
    return cast(
        ToolRuntime,
        SimpleNamespace(
            execution_info=SimpleNamespace(thread_id=thread_id),
        ),
    )


def test_list_workspace_uses_server_thread_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    workspace = tmp_path / "workspaces" / "thread-one"
    workspace.mkdir(parents=True)
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
    runtime = cast(
        ToolRuntime,
        SimpleNamespace(
            execution_info=SimpleNamespace(thread_id="thread-one"),
            context={"workspace_path": "/etc"},
        ),
    )

    assert list_workspace.func(runtime) == ["hello.txt"]


def test_read_file_uses_runtime_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    workspace = tmp_path / "workspaces" / "thread-one"
    workspace.mkdir(parents=True)
    (workspace / "hello.txt").write_text(
        "hello",
        encoding="utf-8",
    )

    runtime = runtime_for("thread-one")

    assert (
        read_file.func(
            "hello.txt",
            runtime,
        )
        == "hello"
    )


def test_read_file_returns_recoverable_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    runtime = runtime_for("thread-one")

    assert read_file.func("../missing.txt", runtime) == (
        "Error: Path escapes the workspace"
    )


def test_tools_fail_closed_without_execution_info() -> None:
    runtime = cast(ToolRuntime, SimpleNamespace(execution_info=None))

    assert list_workspace.func(runtime) == (
        "Error: Server thread identity is unavailable"
    )
    assert read_file.func("hello.txt", runtime) == (
        "Error: Server thread identity is unavailable"
    )


def test_tools_fail_closed_without_thread_id() -> None:
    runtime = runtime_for(None)

    assert list_workspace.func(runtime) == (
        "Error: Server thread identity is unavailable"
    )


def test_client_context_cannot_change_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path / "server-home"))
    client_workspace = tmp_path / "client-workspace"
    client_workspace.mkdir()
    (client_workspace / "secret.txt").write_text("secret", encoding="utf-8")
    runtime = cast(
        ToolRuntime,
        SimpleNamespace(
            execution_info=SimpleNamespace(thread_id="thread-one"),
            context={"workspace_path": str(client_workspace)},
        ),
    )

    assert list_workspace.func(runtime) == []
    assert read_file.func("secret.txt", runtime) == (
        "Error: File not found: secret.txt"
    )
