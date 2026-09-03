from types import SimpleNamespace
from typing import Any, cast

import pytest
from langchain.tools import ToolRuntime
from zharness.sandbox.protocol import (
    DeleteResult,
    EditResult,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from zharness.sandbox.workspace import SandboxWorkspace, SandboxWorkspaceError
from zharness.tools import workspace as workspace_module
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


class RecordingSandbox:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def ls(self, path: str) -> LsResult:
        self.calls.append(("ls", path))
        return LsResult(
            entries=[
                {
                    "path": "/workspace/notes/result.txt",
                    "is_dir": False,
                    "size": 6,
                },
                {"path": "/workspace/notes/archive", "is_dir": True},
            ]
        )

    def read(self, path: str, offset: int, limit: int) -> ReadResult:
        self.calls.append(("read", path, offset, limit))
        return ReadResult(file_data={"content": "two", "encoding": "utf-8"})

    def write(self, path: str, content: str) -> WriteResult:
        self.calls.append(("write", path, content))
        return WriteResult(path=path)

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        self.calls.append(("edit", path, old_string, new_string, replace_all))
        return EditResult(path=path, occurrences=2 if replace_all else 1)

    def delete(self, path: str) -> DeleteResult:
        self.calls.append(("delete", path))
        return DeleteResult(path=path)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        self.calls.append(("glob", pattern, path))
        return GlobResult(
            matches=[{"path": "/workspace/notes/result.txt", "is_dir": False}]
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        self.calls.append(("grep", pattern, path, glob, max_count))
        return GrepResult(
            matches=[
                {
                    "path": "/workspace/notes/result.txt",
                    "line": 2,
                    "text": "needle",
                }
            ]
        )


def install_manager(monkeypatch: pytest.MonkeyPatch, sandbox: object) -> list[str]:
    thread_ids: list[str] = []

    def for_thread(thread_id: str) -> object:
        thread_ids.append(thread_id)
        return sandbox

    manager = SimpleNamespace(for_thread=for_thread)
    monkeypatch.setattr(workspace_module, "get_sandbox_manager", lambda: manager)
    return thread_ids


def make_workspace(sandbox: object) -> SandboxWorkspace:
    return SandboxWorkspace(cast(Any, sandbox))


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


def test_tools_use_one_thread_sandbox_and_canonical_workspace_paths(
    monkeypatch,
) -> None:
    sandbox = RecordingSandbox()
    thread_ids = install_manager(monkeypatch, sandbox)
    runtime = runtime_for("thread-one")

    assert write_file.func(
        "/workspace/notes/result.txt", "one\ntwo", runtime=runtime
    ) == ("Wrote 7 bytes to /workspace/notes/result.txt")
    assert (
        read_file.func(
            "/workspace/notes/result.txt", offset=1, limit=1, runtime=runtime
        )
        == "two"
    )
    assert (
        edit_file.func("/workspace/notes/result.txt", "two", "needle", runtime=runtime)
        == "Replaced 1 occurrence(s) in /workspace/notes/result.txt"
    )
    assert glob_files.func("*.txt", "/workspace/notes", runtime=runtime) == [
        "/workspace/notes/result.txt"
    ]
    assert grep_files.func("needle", runtime=runtime) == [
        {"path": "/workspace/notes/result.txt", "line": 2, "text": "needle"}
    ]
    assert list_workspace.func("/workspace/notes", runtime=runtime) == [
        {"path": "/workspace/notes/result.txt", "is_dir": False, "size": 6},
        {"path": "/workspace/notes/archive/", "is_dir": True},
    ]
    assert delete_path.func("/workspace/notes", runtime=runtime) == (
        "Deleted /workspace/notes"
    )

    assert thread_ids == ["thread-one"] * 7
    assert sandbox.calls == [
        ("write", "/workspace/notes/result.txt", "one\ntwo"),
        ("read", "/workspace/notes/result.txt", 1, 1),
        ("edit", "/workspace/notes/result.txt", "two", "needle", False),
        ("glob", "*.txt", "/workspace/notes"),
        ("grep", "needle", "/workspace", None, None),
        ("ls", "/workspace/notes"),
        ("delete", "/workspace/notes"),
    ]


@pytest.mark.parametrize(
    "path",
    ["relative.txt", "../secret", "a/../../secret", "/outside", "~/.ssh", "a\0b"],
)
def test_adapter_rejects_unsafe_paths_before_backend_access(path: str) -> None:
    sandbox = RecordingSandbox()
    workspace = make_workspace(sandbox)

    with pytest.raises(SandboxWorkspaceError):
        workspace.read(path)
    assert sandbox.calls == []


def test_adapter_rejects_legacy_root_relative_path() -> None:
    sandbox = RecordingSandbox()
    workspace = make_workspace(sandbox)

    with pytest.raises(SandboxWorkspaceError, match="under /workspace"):
        workspace.write("/legacy.txt", "data")
    assert sandbox.calls == []


def test_edit_tool_rejects_legacy_path(monkeypatch) -> None:
    sandbox = RecordingSandbox()
    install_manager(monkeypatch, sandbox)

    result = edit_file.func(
        "/legacy.txt",
        "before",
        "after",
        runtime=runtime_for("thread-one"),
    )

    assert result == "Error: Path must be under /workspace"
    assert sandbox.calls == []


def test_tool_errors_are_recoverable_and_root_delete_is_blocked(monkeypatch) -> None:
    sandbox = RecordingSandbox()
    install_manager(monkeypatch, sandbox)
    runtime = runtime_for("thread-one")

    assert read_file.func("../missing.txt", runtime=runtime) == (
        "Error: Path traversal is not allowed"
    )
    assert delete_path.func("/workspace", runtime=runtime) == (
        "Error: Cannot delete the workspace root"
    )
    assert sandbox.calls == []


def test_adapter_rejects_backend_paths_outside_workspace() -> None:
    sandbox = RecordingSandbox()
    sandbox.ls = lambda path: LsResult(entries=[{"path": "/etc/passwd"}])  # type: ignore[method-assign]
    workspace = make_workspace(sandbox)

    with pytest.raises(SandboxWorkspaceError, match="outside the workspace"):
        workspace.ls()


def test_adapter_rejects_binary_reads() -> None:
    sandbox = RecordingSandbox()
    sandbox.read = lambda path, offset, limit: ReadResult(  # type: ignore[method-assign]
        file_data={"content": "AA==", "encoding": "base64"}
    )
    workspace = make_workspace(sandbox)

    with pytest.raises(SandboxWorkspaceError, match="not UTF-8"):
        workspace.read("/workspace/binary.bin")


def test_tools_fail_closed_without_server_thread_identity(monkeypatch) -> None:
    sandbox = RecordingSandbox()
    thread_ids = install_manager(monkeypatch, sandbox)
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
    assert thread_ids == []


def test_client_context_cannot_change_sandbox(monkeypatch) -> None:
    sandbox = RecordingSandbox()
    thread_ids = install_manager(monkeypatch, sandbox)
    runtime = cast(
        ToolRuntime,
        SimpleNamespace(
            execution_info=SimpleNamespace(thread_id="thread-one"),
            context={"workspace_path": "/untrusted/client/path"},
        ),
    )

    list_workspace.func(runtime=runtime)

    assert thread_ids == ["thread-one"]
    assert sandbox.calls == [("ls", "/workspace")]
