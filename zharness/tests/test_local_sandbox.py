"""Tests for the local filesystem sandbox backend."""

import os
import threading
import time
from pathlib import Path

import pytest
from zharness.sandbox.local import (
    LocalSandbox,
    LocalSandboxError,
    LocalSandboxManager,
    LocalSandboxSettings,
)
from zharness.sandbox.workspace import SandboxWorkspace


@pytest.fixture
def root(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def sandbox(root: Path) -> LocalSandbox:
    return LocalSandbox(root)


# ---------------------------------------------------------------------------
# Path confinement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/workspace/../secret",
        "/workspace/a/../../secret",
        "/etc/passwd",
        "/",
        "/other/root",
        "/workspace/\0bad",
    ],
)
def test_rejects_paths_outside_workspace(sandbox: LocalSandbox, path: str) -> None:
    with pytest.raises(LocalSandboxError):
        sandbox.resolve_path(path)


def test_rejects_symlink_escape(root: Path) -> None:
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    sandbox = LocalSandbox(root)

    with pytest.raises(LocalSandboxError, match="escapes"):
        sandbox.resolve_path("/workspace/escape/secret.txt")


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


def test_write_read_edit_delete_roundtrip(sandbox: LocalSandbox) -> None:
    result = sandbox.write("/workspace/docs/guide.txt", "alpha\nbeta")
    assert result.error is None
    assert result.path == "/workspace/docs/guide.txt"

    read = sandbox.read("/workspace/docs/guide.txt", offset=1, limit=1)
    assert read.error is None
    assert read.file_data["content"] == "beta"
    assert read.total_lines == 2
    assert read.start_line == 2
    assert read.end_line == 2
    assert read.next_offset is None

    edit = sandbox.edit("/workspace/docs/guide.txt", "beta", "needle")
    assert edit.error is None
    assert edit.occurrences == 1
    assert sandbox.read("/workspace/docs/guide.txt").file_data["content"] == (
        "alpha\nneedle"
    )

    grep = sandbox.grep("needle", path="/workspace/docs")
    assert grep.error is None
    assert grep.matches == [
        {"path": "/workspace/docs/guide.txt", "line": 2, "text": "needle"}
    ]

    entries = sandbox.ls("/workspace/docs")
    assert entries.error is None
    assert [(e["path"], e["is_dir"], e["size"]) for e in entries.entries] == [
        ("/workspace/docs/guide.txt", False, 12)
    ]

    assert sandbox.glob("*.txt", path="/workspace/docs").matches == [
        {"path": "/workspace/docs/guide.txt"}
    ]

    deleted = sandbox.delete("/workspace/docs")
    assert deleted.error is None
    assert deleted.path == "/workspace/docs"


def test_read_pagination_beyond_eof_is_consistent(sandbox: LocalSandbox) -> None:
    sandbox.write("/workspace/f.txt", "one\ntwo\nthree")
    result = sandbox.read("/workspace/f.txt", offset=10, limit=5)
    assert result.error == "Line offset 10 exceeds file length (3 lines)"


def test_read_handles_empty_files_and_degenerate_windows(sandbox: LocalSandbox) -> None:
    sandbox.write("/workspace/empty.txt", "")
    empty = sandbox.read("/workspace/empty.txt")
    assert empty.error is None
    assert empty.file_data == {"content": "", "encoding": "utf-8"}
    assert empty.start_line is None

    sandbox.write("/workspace/content.txt", "one\ntwo")
    negative_limit = sandbox.read("/workspace/content.txt", limit=-1)
    assert negative_limit.error is None
    assert negative_limit.file_data["content"] == ""
    assert negative_limit.no_lines_requested is True


def test_read_rejects_binary_and_missing_files(
    sandbox: LocalSandbox, root: Path
) -> None:
    (root / "bin.dat").write_bytes(b"\xff\xfe\x00binary")
    assert "not UTF-8" in sandbox.read("/workspace/bin.dat").error
    assert "not found" in sandbox.read("/workspace/missing.txt").error


def test_write_edit_failures(sandbox: LocalSandbox) -> None:
    assert "must not be empty" in sandbox.edit("/workspace/f.txt", "", "x").error
    sandbox.write("/workspace/f.txt", "a\nb\na")
    result = sandbox.edit("/workspace/f.txt", "a", "x")
    assert result.error is not None and "not unique" in result.error
    edit_all = sandbox.edit("/workspace/f.txt", "a", "x", replace_all=True)
    assert edit_all.occurrences == 2


def test_delete_root_is_blocked(sandbox: LocalSandbox) -> None:
    assert "workspace root" in sandbox.delete("/workspace").error


def test_upload_and_download(sandbox: LocalSandbox) -> None:
    upload = sandbox.upload_files([("/workspace/nested/upload.txt", b"uploaded")])
    assert upload[0].error is None
    download = sandbox.download_files(["/workspace/nested/upload.txt"])
    assert download[0].error is None
    assert download[0].content == b"uploaded"

    assert sandbox.download_files(["/workspace/nope.txt"])[0].error == "file_not_found"
    assert sandbox.download_files(["/workspace"])[0].error == "is_directory"


def test_download_rejects_fifo_without_blocking(
    sandbox: LocalSandbox, root: Path
) -> None:
    os.mkfifo(root / "pipe")
    result = sandbox.download_files(["/workspace/pipe"])[0]
    assert result.error == "not a regular file"


def test_glob_and_grep_follow_shared_pattern_contract(root: Path) -> None:
    (root / "top.py").write_text("needle", encoding="utf-8")
    (root / "top.txt").write_text("needle", encoding="utf-8")
    nested = root / "src" / "sub"
    nested.mkdir(parents=True)
    (root / "src" / "direct.py").write_text("needle", encoding="utf-8")
    (nested / "nested.py").write_text("needle", encoding="utf-8")
    sandbox = LocalSandbox(root)

    assert sandbox.glob("/*.py").matches == [{"path": "/workspace/top.py"}]
    assert sandbox.glob("*.{py,txt}").matches == [
        {"path": "/workspace/src/direct.py"},
        {"path": "/workspace/src/sub/nested.py"},
        {"path": "/workspace/top.py"},
        {"path": "/workspace/top.txt"},
    ]
    assert [
        match["path"] for match in sandbox.grep("needle", glob="src/*.py").matches
    ] == ["/workspace/src/direct.py"]


def test_result_caps_only_report_confirmed_truncation(root: Path) -> None:
    for name in ("a.py", "b.py"):
        (root / name).write_text("needle", encoding="utf-8")
    sandbox = LocalSandbox(root, max_results=2)

    glob_result = sandbox.glob("*.py")
    assert len(glob_result.matches) == 2
    assert glob_result.truncated is False

    grep_result = sandbox.grep("needle", max_count=2)
    assert len(grep_result.matches) == 2
    assert grep_result.truncated is False

    zero_result = sandbox.grep("needle", max_count=0)
    assert zero_result.matches == []
    assert zero_result.truncated is True

    (root / "c.py").write_text("needle", encoding="utf-8")
    assert sandbox.glob("*.py").truncated is True
    assert sandbox.grep("needle", max_count=2).truncated is True


# ---------------------------------------------------------------------------
# Host bash execution
# ---------------------------------------------------------------------------


def test_execute_disabled_by_default(sandbox: LocalSandbox) -> None:
    result = sandbox.execute("echo hi")
    assert result.exit_code == 1
    assert "disabled" in result.output


def test_execute_runs_when_enabled(root: Path) -> None:
    sandbox = LocalSandbox(root, allow_host_bash=True)
    result = sandbox.execute("printf 'hello from host'")
    assert result.exit_code == 0
    assert result.output == "hello from host"


def test_execute_times_out(root: Path) -> None:
    sandbox = LocalSandbox(root, allow_host_bash=True)
    result = sandbox.execute("sleep 5", timeout=1)
    assert result.exit_code == 124
    assert result.truncated


def test_execute_timeout_kills_background_process_group(root: Path) -> None:
    sandbox = LocalSandbox(root, allow_host_bash=True)
    result = sandbox.execute(
        "nohup sh -c 'sleep 1; printf survived > orphan-marker' >/dev/null 2>&1 & wait",
        timeout=0.1,
    )
    assert result.exit_code == 124
    time.sleep(1.1)
    assert not (root / "orphan-marker").exists()


# ---------------------------------------------------------------------------
# SandboxWorkspace integration and manager
# ---------------------------------------------------------------------------


def test_workspace_adapter_roundtrip(root: Path) -> None:
    workspace = SandboxWorkspace(LocalSandbox(root))
    assert workspace.write("/notes/result.txt", "one\ntwo") == "/notes/result.txt"
    assert workspace.read("/notes/result.txt", offset=1, limit=1) == "two"
    assert workspace.glob("*.txt", path="/notes") == ["/notes/result.txt"]
    assert workspace.grep("one", path="/notes") == [
        {"path": "/notes/result.txt", "line": 1, "text": "one"}
    ]
    assert [entry["path"] for entry in workspace.ls("/notes")] == ["/notes/result.txt"]
    assert workspace.delete("/notes") == "/notes"


def test_local_manager_per_thread_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ZHARNESS_HOME", str(tmp_path))
    manager = LocalSandboxManager(settings=LocalSandboxSettings(root=None))
    first = manager.for_thread("thread-a")
    second = manager.for_thread("thread-a")
    other = manager.for_thread("thread-b")
    assert first is second
    assert first.root == tmp_path / "workspaces" / "thread-a"
    assert other.root == tmp_path / "workspaces" / "thread-b"

    assert manager.remove_for_thread("thread-a") is True
    assert manager.remove_for_thread("thread-a") is False
    assert manager.stop_all() == []


def test_local_manager_shared_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    manager = LocalSandboxManager(
        settings=LocalSandboxSettings(root=str(project), allow_host_bash=True)
    )
    sandbox = manager.for_thread("thread-a")
    assert sandbox.root == project.resolve()
    assert sandbox.allow_host_bash is True
    assert manager.for_thread("thread-b").root == project.resolve()

    assert manager.remove_for_thread("thread-a") is True
    assert project.exists()


def test_local_manager_stops_running_commands(tmp_path: Path) -> None:
    manager = LocalSandboxManager(
        settings=LocalSandboxSettings(
            root=str(tmp_path),
            allow_host_bash=True,
        )
    )
    sandbox = manager.for_thread("thread-a")
    thread = threading.Thread(
        target=sandbox.execute,
        args=("printf started > started; sleep 10",),
    )
    thread.start()
    deadline = time.monotonic() + 2
    while not (tmp_path / "started").exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert (tmp_path / "started").exists()
    assert manager.stop_all() == [sandbox.id]
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_provider_selection(monkeypatch) -> None:
    import zharness.sandbox.manager as manager_module

    monkeypatch.setenv("ZHARNESS_SANDBOX_PROVIDER", "local")
    manager_module._manager = None
    selected = manager_module.get_sandbox_manager()
    assert isinstance(selected, LocalSandboxManager)

    monkeypatch.setenv("ZHARNESS_SANDBOX_PROVIDER", "docker")
    manager_module._manager = None
    selected = manager_module.get_sandbox_manager()
    from zharness.sandbox.manager import DockerSandboxManager

    assert isinstance(selected, DockerSandboxManager)


def test_local_sandbox_requires_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(LocalSandboxError, match="does not exist"):
        LocalSandbox(tmp_path / "missing")

    file_only = tmp_path / "file.txt"
    file_only.write_text("x", encoding="utf-8")
    with pytest.raises(LocalSandboxError, match="not a directory"):
        LocalSandbox(file_only)
