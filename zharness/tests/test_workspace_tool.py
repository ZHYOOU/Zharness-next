from pathlib import Path
from types import SimpleNamespace
from typing import cast

from langchain.tools import ToolRuntime
from zharness.tools.workspace import (
    delete_path,
    edit_file,
    glob_files,
    grep_files,
    list_workspace,
    read_file,
    write_file,
)


def runtime_for(thread_id: str | None) -> ToolRuntime:
    return cast(
        ToolRuntime,
        SimpleNamespace(execution_info=SimpleNamespace(thread_id=thread_id)),
    )


def test_runtime_is_hidden_from_all_model_schemas() -> None:
    assert set(list_workspace.args) == {"path"}
    assert set(read_file.args) == {"path", "offset", "limit"}
    assert set(write_file.args) == {"path", "content"}
    assert set(edit_file.args) == {
        "path",
        "old_string",
        "new_string",
        "replace_all",
    }
    assert set(delete_path.args) == {"path"}
    assert set(glob_files.args) == {"pattern", "path"}
    assert set(grep_files.args) == {"pattern", "path", "include"}


def test_tools_use_server_thread_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    runtime = runtime_for("thread-one")

    assert write_file.func("/notes/result.txt", "one\ntwo", runtime=runtime) == (
        "Wrote 7 bytes to /notes/result.txt"
    )
    assert (
        read_file.func("/notes/result.txt", offset=1, limit=1, runtime=runtime) == "two"
    )
    assert (
        edit_file.func("/notes/result.txt", "two", "needle", runtime=runtime)
        == "Replaced 1 occurrence(s) in /notes/result.txt"
    )
    assert glob_files.func("*.txt", "/notes", runtime=runtime) == ["/notes/result.txt"]
    assert grep_files.func("needle", runtime=runtime) == [
        {"path": "/notes/result.txt", "line": 2, "text": "needle"}
    ]

    entries = list_workspace.func("/notes", runtime=runtime)
    assert isinstance(entries, list)
    assert entries[0]["path"] == "/notes/result.txt"
    assert delete_path.func("/notes", runtime=runtime) == "Deleted /notes"
    assert not (tmp_path / "workspaces" / "thread-one" / "notes").exists()
    assert not (tmp_path / "workspaces" / "thread-two").exists()


def test_tool_errors_are_recoverable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    runtime = runtime_for("thread-one")

    assert read_file.func("../missing.txt", runtime=runtime) == (
        "Error: Path traversal is not allowed"
    )
    assert delete_path.func("/", runtime=runtime) == (
        "Error: Cannot delete the workspace root"
    )


def test_tools_fail_closed_without_server_thread_identity() -> None:
    missing_execution = cast(ToolRuntime, SimpleNamespace(execution_info=None))
    missing_thread = runtime_for(None)

    for runtime in [missing_execution, missing_thread]:
        assert list_workspace.func(runtime=runtime) == (
            "Error: Server thread identity is unavailable"
        )
        assert read_file.func("hello.txt", runtime=runtime) == (
            "Error: Server thread identity is unavailable"
        )
        assert write_file.func("hello.txt", "hello", runtime=runtime) == (
            "Error: Server thread identity is unavailable"
        )


def test_client_context_cannot_change_workspace(tmp_path: Path, monkeypatch) -> None:
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

    assert list_workspace.func(runtime=runtime) == []
    assert read_file.func("secret.txt", runtime=runtime) == (
        "Error: File not found: secret.txt"
    )
