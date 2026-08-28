from pathlib import Path

import pytest
from zharness.workspace.reader import (
    WorkspaceReadError,
    read_workspace_file,
)


def test_read_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text(
        "hello",
        encoding="utf-8",
    )

    assert (
        read_workspace_file(
            str(workspace),
            "hello.txt",
        )
        == "hello"
    )


def test_read_nested_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "docs"
    nested.mkdir(parents=True)
    (nested / "guide.txt").write_text(
        "guide",
        encoding="utf-8",
    )

    assert (
        read_workspace_file(
            str(workspace),
            "docs/guide.txt",
        )
        == "guide"
    )


def test_rejects_absolute_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(
        WorkspaceReadError,
        match="Absolute paths",
    ):
        read_workspace_file(
            str(workspace),
            str(outside),
        )


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "outside.txt").write_text(
        "secret",
        encoding="utf-8",
    )

    with pytest.raises(
        WorkspaceReadError,
        match="escapes",
    ):
        read_workspace_file(
            str(workspace),
            "../outside.txt",
        )


def test_rejects_parent_traversal_before_checking_existence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(
        WorkspaceReadError,
        match="escapes",
    ):
        read_workspace_file(
            str(workspace),
            "../missing.txt",
        )


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    link = workspace / "link.txt"

    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    with pytest.raises(
        WorkspaceReadError,
        match="escapes",
    ):
        read_workspace_file(
            str(workspace),
            "link.txt",
        )


def test_rejects_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    directory = workspace / "docs"
    directory.mkdir(parents=True)

    with pytest.raises(
        WorkspaceReadError,
        match="not a regular file",
    ):
        read_workspace_file(
            str(workspace),
            "docs",
        )


def test_rejects_oversized_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "large.txt").write_text(
        "12345",
        encoding="utf-8",
    )

    with pytest.raises(
        WorkspaceReadError,
        match="exceeds",
    ):
        read_workspace_file(
            str(workspace),
            "large.txt",
            max_bytes=4,
        )


def test_rejects_non_utf8_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "binary.bin").write_bytes(b"\xff\xfe")

    with pytest.raises(
        WorkspaceReadError,
        match="UTF-8",
    ):
        read_workspace_file(
            str(workspace),
            "binary.bin",
        )
