from pathlib import Path

import pytest
from zharness.workspace.listing import (
    MAX_WORKSPACE_ENTRIES,
    list_workspace_entries,
)


def test_list_workspace_entries_lists_files_and_directories(tmp_path: Path) -> None:
    (tmp_path / "Beta").mkdir()
    (tmp_path / "alpha.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "zeta.txt").write_text("zeta", encoding="utf-8")

    assert list_workspace_entries(str(tmp_path)) == [
        "alpha.txt",
        "Beta/",
        "zeta.txt",
    ]


def test_list_workspace_entries_does_not_recurse(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "hidden.txt").write_text("hidden", encoding="utf-8")

    assert list_workspace_entries(str(tmp_path)) == ["nested/"]


def test_list_workspace_entries_applies_entry_limit(tmp_path: Path) -> None:
    for index in range(MAX_WORKSPACE_ENTRIES + 5):
        (tmp_path / f"file-{index:03}.txt").touch()

    entries = list_workspace_entries(str(tmp_path))

    assert len(entries) == MAX_WORKSPACE_ENTRIES
    assert entries[0] == "file-000.txt"
    assert entries[-1] == "file-099.txt"


def test_list_workspace_entries_rejects_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(FileNotFoundError):
        list_workspace_entries(str(missing))


def test_list_workspace_entries_rejects_file_path(tmp_path: Path) -> None:
    file_path = tmp_path / "file.txt"
    file_path.write_text("content", encoding="utf-8")

    with pytest.raises(NotADirectoryError):
        list_workspace_entries(str(file_path))
