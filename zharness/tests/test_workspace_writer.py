from pathlib import Path

import pytest
from zharness.workspace.writer import (
    WorkspaceWriteError,
    write_workspace_file,
)


def test_write_workspace_file_creates_utf8_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = write_workspace_file(workspace, "hello.txt", "你好")

    assert result == "Wrote 6 bytes to hello.txt"
    assert (workspace / "hello.txt").read_text(encoding="utf-8") == "你好"


def test_write_workspace_file_creates_parent_directories(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    write_workspace_file(workspace, "docs/guide.txt", "guide")

    assert (workspace / "docs" / "guide.txt").read_text() == "guide"


def test_write_workspace_file_overwrites_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "hello.txt"
    target.write_text("old", encoding="utf-8")

    write_workspace_file(workspace, "hello.txt", "new")

    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("path", ["../outside.txt", "/tmp/outside.txt"])
def test_write_workspace_file_rejects_unsafe_path(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(WorkspaceWriteError):
        write_workspace_file(workspace, path, "secret")


def test_write_workspace_file_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    try:
        (workspace / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    with pytest.raises(WorkspaceWriteError, match="escapes"):
        write_workspace_file(workspace, "link/secret.txt", "secret")

    assert not (outside / "secret.txt").exists()


def test_write_workspace_file_rejects_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)

    with pytest.raises(WorkspaceWriteError, match="not a regular file"):
        write_workspace_file(workspace, "docs", "content")


def test_write_workspace_file_rejects_oversized_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(WorkspaceWriteError, match="exceeds"):
        write_workspace_file(workspace, "large.txt", "你好", max_bytes=5)

    assert not (workspace / "large.txt").exists()
