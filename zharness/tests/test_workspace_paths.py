from pathlib import Path

import pytest
from zharness.host.paths import (
    WorkspacePathError,
    ensure_thread_workspace,
    thread_workspace_path,
)


def test_thread_workspace_path_uses_server_home(tmp_path: Path) -> None:
    assert (
        thread_workspace_path("thread-123", home=tmp_path)
        == (tmp_path / "workspaces" / "thread-123").resolve()
    )


def test_ensure_thread_workspace_creates_isolated_directories(
    tmp_path: Path,
) -> None:
    first = ensure_thread_workspace("thread-one", home=tmp_path)
    second = ensure_thread_workspace("thread-two", home=tmp_path)

    assert first.is_dir()
    assert second.is_dir()
    assert first != second


def test_thread_workspace_path_rejects_symlink_escape(tmp_path: Path) -> None:
    workspaces = tmp_path / "workspaces"
    workspaces.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    try:
        (workspaces / "thread-one").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    with pytest.raises(WorkspacePathError, match="escapes"):
        thread_workspace_path("thread-one", home=tmp_path)


@pytest.mark.parametrize(
    "thread_id",
    ["", "../escape", "/absolute", "with/slash", "with space", "a" * 129],
)
def test_thread_workspace_path_rejects_invalid_thread_id(
    tmp_path: Path,
    thread_id: str,
) -> None:
    with pytest.raises(WorkspacePathError, match="Invalid thread ID"):
        thread_workspace_path(thread_id, home=tmp_path)
