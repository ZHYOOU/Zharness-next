from pathlib import Path

import pytest
from zharness.workspace.filesystem import (
    WorkspaceFilesystem,
    WorkspaceFilesystemError,
)


def filesystem(tmp_path: Path, **kwargs) -> WorkspaceFilesystem:
    root = tmp_path / "workspace"
    root.mkdir()
    return WorkspaceFilesystem(root, **kwargs)


def test_virtual_paths_are_anchored_to_workspace(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)

    assert fs.resolve("notes/file.txt") == fs.root / "notes" / "file.txt"
    assert fs.resolve("/notes/file.txt") == fs.root / "notes" / "file.txt"
    assert fs.resolve("/") == fs.root
    assert fs.virtual_path(fs.root / "notes" / "file.txt") == "/notes/file.txt"


@pytest.mark.parametrize("path", ["../secret", "a/../../secret", "~/.ssh", "a\0b"])
def test_resolve_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    fs = filesystem(tmp_path)

    with pytest.raises(WorkspaceFilesystemError):
        fs.resolve(path)


def test_resolve_rejects_symlink_escape(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (fs.root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    with pytest.raises(WorkspaceFilesystemError, match="escapes"):
        fs.resolve("/link/secret.txt")


def test_ls_returns_sorted_metadata_and_does_not_recurse(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    (fs.root / "Beta").mkdir()
    (fs.root / "Beta" / "nested.txt").write_text("nested", encoding="utf-8")
    (fs.root / "alpha.txt").write_text("alpha", encoding="utf-8")

    entries = fs.ls()

    assert [entry["path"] for entry in entries] == ["/alpha.txt", "/Beta/"]
    assert entries[0]["size"] == 5
    assert entries[0]["is_dir"] is False
    assert entries[1]["size"] == 0
    assert entries[1]["is_dir"] is True
    assert entries[0]["modified_at"].endswith("+00:00")


def test_ls_applies_result_limit_and_skips_escaping_links(tmp_path: Path) -> None:
    fs = filesystem(tmp_path, max_results=2)
    for name in ["a.txt", "b.txt", "c.txt"]:
        (fs.root / name).touch()
    try:
        (fs.root / "00-link").symlink_to(tmp_path / "outside")
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    assert [entry["path"] for entry in fs.ls()] == ["/a.txt", "/b.txt"]


def test_read_supports_line_pagination_and_preserves_newlines(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    (fs.root / "lines.txt").write_text("one\ntwo\nthree", encoding="utf-8")

    assert fs.read("/lines.txt", offset=1, limit=1) == "two\n"
    assert fs.read("lines.txt", offset=-10, limit=2) == "one\ntwo\n"
    assert fs.read("lines.txt", limit=0) == ""


def test_read_rejects_directories_large_and_binary_files(tmp_path: Path) -> None:
    fs = filesystem(tmp_path, max_file_bytes=4)
    (fs.root / "large.txt").write_text("12345", encoding="utf-8")
    (fs.root / "binary.bin").write_bytes(b"\xff")

    with pytest.raises(WorkspaceFilesystemError, match="regular file"):
        fs.read("/")
    with pytest.raises(WorkspaceFilesystemError, match="exceeds"):
        fs.read("large.txt")
    with pytest.raises(WorkspaceFilesystemError, match="UTF-8"):
        fs.read("binary.bin")


def test_write_creates_parents_and_atomically_overwrites(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)

    assert fs.write("/docs/guide.txt", "你好") == "/docs/guide.txt"
    assert (fs.root / "docs" / "guide.txt").read_text(encoding="utf-8") == "你好"

    fs.write("docs/guide.txt", "updated")

    assert (fs.root / "docs" / "guide.txt").read_text(encoding="utf-8") == "updated"
    assert not list((fs.root / "docs").glob(".guide.txt.*.tmp"))


def test_write_rejects_root_directory_large_content_and_symlink_escape(
    tmp_path: Path,
) -> None:
    fs = filesystem(tmp_path, max_file_bytes=4)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (fs.root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    with pytest.raises(WorkspaceFilesystemError, match="root"):
        fs.write("/", "x")
    with pytest.raises(WorkspaceFilesystemError, match="exceeds"):
        fs.write("large.txt", "12345")
    with pytest.raises(WorkspaceFilesystemError, match="escapes"):
        fs.write("link/secret.txt", "x")
    assert not (outside / "secret.txt").exists()


def test_edit_requires_unique_match_unless_replace_all(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    fs.write("notes.txt", "old and old")

    with pytest.raises(WorkspaceFilesystemError, match="not unique"):
        fs.edit("notes.txt", "old", "new")

    assert fs.edit("notes.txt", "old", "new", replace_all=True) == 2
    assert fs.read("notes.txt") == "new and new"


def test_delete_file_and_directory_but_not_root(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    fs.write("tree/a.txt", "a")
    fs.write("single.txt", "x")

    assert fs.delete("single.txt") == "/single.txt"
    assert fs.delete("/tree") == "/tree"
    assert not (fs.root / "tree").exists()
    with pytest.raises(WorkspaceFilesystemError, match="root"):
        fs.delete("/")


def test_delete_unlinks_symlink_without_deleting_target(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    link = fs.root / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    assert fs.delete("link.txt") == "/link.txt"
    assert not link.exists()
    assert target.read_text(encoding="utf-8") == "keep"


def test_delete_rejects_path_through_escaping_parent_symlink(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    try:
        (fs.root / "outside").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    with pytest.raises(WorkspaceFilesystemError, match="escapes"):
        fs.delete("outside/keep.txt")
    assert target.read_text(encoding="utf-8") == "keep"


def test_write_does_not_follow_final_symlink(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    target = fs.root / "target.txt"
    target.write_text("keep", encoding="utf-8")
    link = fs.root / "link.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symbolic links are unavailable")

    with pytest.raises(WorkspaceFilesystemError, match="symlink"):
        fs.write("link.txt", "replace")
    assert target.read_text(encoding="utf-8") == "keep"


def test_glob_is_sorted_scoped_and_rejects_traversal(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    fs.write("src/b.py", "")
    fs.write("src/a.py", "")
    fs.write("tests/test_a.py", "")

    assert fs.glob("*.py", path="/src") == ["/src/a.py", "/src/b.py"]
    assert fs.glob("**/*.py") == [
        "/src/a.py",
        "/src/b.py",
        "/tests/test_a.py",
    ]
    with pytest.raises(WorkspaceFilesystemError, match="Unsafe"):
        fs.glob("../*.py")


def test_grep_returns_literal_matches_with_include_filter(tmp_path: Path) -> None:
    fs = filesystem(tmp_path)
    fs.write("src/app.py", "needle one\nno match\nneedle two\n")
    fs.write("src/app.txt", "needle ignored\n")

    assert fs.grep("needle", path="/src", include="*.py") == [
        {"path": "/src/app.py", "line": 1, "text": "needle one"},
        {"path": "/src/app.py", "line": 3, "text": "needle two"},
    ]


def test_grep_skips_binary_and_large_files_and_caps_results(tmp_path: Path) -> None:
    fs = filesystem(tmp_path, max_file_bytes=8, max_results=1)
    (fs.root / "binary.bin").write_bytes(b"needle\xff")
    (fs.root / "large.txt").write_text("needle---", encoding="utf-8")
    (fs.root / "small.txt").write_text("needle\n", encoding="utf-8")

    assert fs.grep("needle") == [{"path": "/small.txt", "line": 1, "text": "needle"}]
