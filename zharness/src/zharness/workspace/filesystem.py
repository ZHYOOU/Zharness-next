"""Thread-workspace filesystem with virtual, root-confined paths."""

from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TypedDict

DEFAULT_MAX_FILE_BYTES = 256 * 1024
DEFAULT_MAX_RESULTS = 100


class WorkspaceFilesystemError(ValueError):
    """Raised when a workspace operation cannot be completed safely."""


class FileInfo(TypedDict):
    """Agent-facing metadata for a workspace entry."""

    path: str
    is_dir: bool
    size: int
    modified_at: str


class GrepMatch(TypedDict):
    """A literal text match in a workspace file."""

    path: str
    line: int
    text: str


class WorkspaceFilesystem:
    """Read and mutate files beneath one virtual filesystem root.

    Agent-visible paths always use POSIX syntax and start at ``/``. A leading
    slash means the workspace root, never the host filesystem root. Parent
    traversal and home expansion are rejected, and resolved symlinks must stay
    beneath the workspace root.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        if max_results < 1:
            raise ValueError("max_results must be positive")

        try:
            root = Path(root_dir).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceFilesystemError("Workspace does not exist") from exc
        if not root.is_dir():
            raise WorkspaceFilesystemError("Workspace root is not a directory")

        self.root = root
        self.max_file_bytes = max_file_bytes
        self.max_results = max_results

    def resolve(self, path: str) -> Path:
        """Resolve an agent-visible path beneath the workspace root."""

        lexical = self._lexical_path(path)
        try:
            resolved = lexical.resolve(strict=False)
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceFilesystemError("Path escapes the workspace") from exc
        except (OSError, RuntimeError) as exc:
            raise WorkspaceFilesystemError(f"Invalid workspace path: {path}") from exc
        return resolved

    def _lexical_path(self, path: str) -> Path:
        """Map a validated virtual path without following its final symlink."""

        if not isinstance(path, str) or "\0" in path:
            raise WorkspaceFilesystemError("Invalid workspace path")

        virtual_path = path or "/"
        if virtual_path.startswith("~"):
            raise WorkspaceFilesystemError("Home expansion is not allowed")

        parts = PurePosixPath(virtual_path).parts
        if ".." in parts:
            raise WorkspaceFilesystemError("Path traversal is not allowed")

        relative = virtual_path.lstrip("/")
        return self.root / relative

    def virtual_path(self, path: Path) -> str:
        """Convert a host path under the root to an agent-visible path."""

        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceFilesystemError("Path escapes the workspace") from exc
        return "/" if not relative.parts else f"/{relative.as_posix()}"

    def ls(self, path: str = "/") -> list[FileInfo]:
        """List direct children of a directory in deterministic order."""

        directory = self.resolve(path)
        if not directory.exists():
            raise WorkspaceFilesystemError(f"Path not found: {path}")
        if not directory.is_dir():
            raise WorkspaceFilesystemError(f"Not a directory: {path}")

        entries: list[FileInfo] = []
        try:
            children = sorted(
                directory.iterdir(), key=lambda item: item.name.casefold()
            )
            for child in children:
                if len(entries) >= self.max_results:
                    break
                if child.is_symlink():
                    continue
                try:
                    resolved = child.resolve(strict=True)
                    resolved.relative_to(self.root)
                    stat = child.stat()
                except ValueError, OSError, RuntimeError:
                    # Broken and escaping symlinks are not part of the virtual tree.
                    continue
                is_dir = child.is_dir()
                if not is_dir and not child.is_file():
                    continue
                virtual = self.virtual_path(child)
                entries.append(
                    {
                        "path": f"{virtual}/" if is_dir else virtual,
                        "is_dir": is_dir,
                        "size": 0 if is_dir else stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, tz=UTC
                        ).isoformat(),
                    }
                )
        except OSError as exc:
            raise WorkspaceFilesystemError(f"Could not list directory: {path}") from exc
        return entries

    def read(self, path: str, *, offset: int = 0, limit: int = 2000) -> str:
        """Read a UTF-8 file, optionally selecting a zero-based line window."""

        target = self._regular_file(path)
        if limit < 0:
            raise ValueError("limit must be non-negative")

        try:
            if target.stat().st_size > self.max_file_bytes:
                raise WorkspaceFilesystemError(
                    f"File exceeds the {self.max_file_bytes}-byte limit: {path}"
                )
            descriptor = os.open(
                target,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as file:
                content = file.read(self.max_file_bytes + 1)
        except WorkspaceFilesystemError:
            raise
        except OSError as exc:
            raise WorkspaceFilesystemError(f"Could not read file: {path}") from exc

        if len(content) > self.max_file_bytes:
            raise WorkspaceFilesystemError(
                f"File exceeds the {self.max_file_bytes}-byte limit: {path}"
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceFilesystemError(f"File is not UTF-8 text: {path}") from exc

        lines = text.splitlines(keepends=True)
        start = max(offset, 0)
        return "".join(lines[start : start + limit]) if limit else ""

    def write(self, path: str, content: str) -> str:
        """Atomically create or replace a UTF-8 text file."""

        if not isinstance(content, str):
            raise WorkspaceFilesystemError("File content must be text")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise WorkspaceFilesystemError(
                f"Content exceeds the {self.max_file_bytes}-byte limit"
            )

        lexical = self._lexical_path(path)
        if lexical == self.root:
            raise WorkspaceFilesystemError("Cannot write to the workspace root")

        try:
            # Validate the whole unresolved path before mkdir; otherwise a parent
            # symlink could make mkdir create directories outside the workspace.
            self.resolve(path)
            lexical.parent.mkdir(parents=True, exist_ok=True)
            # Resolve again after mkdir so an existing parent symlink cannot escape.
            parent = lexical.parent.resolve(strict=True)
            parent.relative_to(self.root)
            target = parent / lexical.name
            if target.is_symlink():
                raise WorkspaceFilesystemError(f"Cannot overwrite symlink: {path}")
            if target.exists() and not target.is_file():
                raise WorkspaceFilesystemError(f"Not a regular file: {path}")
            descriptor, temporary_name = tempfile.mkstemp(
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as file:
                    file.write(encoded)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        except WorkspaceFilesystemError:
            raise
        except ValueError as exc:
            raise WorkspaceFilesystemError("Path escapes the workspace") from exc
        except OSError as exc:
            raise WorkspaceFilesystemError(f"Could not write file: {path}") from exc
        return self._normalize_virtual(path)

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> int:
        """Replace one unique string, or every occurrence, in a text file."""

        if not old_string:
            raise WorkspaceFilesystemError("old_string must not be empty")
        content = self.read(path, limit=2**31 - 1)
        occurrences = content.count(old_string)
        if occurrences == 0:
            raise WorkspaceFilesystemError("old_string was not found")
        if not replace_all and occurrences != 1:
            raise WorkspaceFilesystemError(
                f"old_string is not unique ({occurrences} occurrences)"
            )
        updated = content.replace(old_string, new_string, -1 if replace_all else 1)
        self.write(path, updated)
        return occurrences if replace_all else 1

    def delete(self, path: str) -> str:
        """Delete a file, symlink, or directory tree without following links."""

        lexical = self._lexical_path(path)
        if lexical == self.root:
            raise WorkspaceFilesystemError("Cannot delete the workspace root")
        try:
            lexical.parent.resolve(strict=True).relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceFilesystemError("Path escapes the workspace") from exc
        except (OSError, RuntimeError) as exc:
            raise WorkspaceFilesystemError(f"Invalid workspace path: {path}") from exc
        if lexical.is_symlink():
            try:
                lexical.unlink()
            except OSError as exc:
                raise WorkspaceFilesystemError(f"Could not delete: {path}") from exc
            return self._normalize_virtual(path)

        target = self.resolve(path)
        if not target.exists():
            raise WorkspaceFilesystemError(f"Path not found: {path}")

        try:
            if not target.is_dir():
                target.unlink()
            else:
                shutil.rmtree(target)
        except OSError as exc:
            raise WorkspaceFilesystemError(f"Could not delete: {path}") from exc
        return self._normalize_virtual(path)

    def glob(self, pattern: str, *, path: str = "/") -> list[str]:
        """Find paths matching a workspace-relative glob pattern."""

        self._validate_pattern(pattern)
        base = self.resolve(path)
        if not base.exists():
            return []
        if not base.is_dir():
            raise WorkspaceFilesystemError(f"Not a directory: {path}")

        matches: list[str] = []
        try:
            candidates = sorted(
                base.glob(pattern),
                key=lambda candidate: candidate.as_posix().casefold(),
            )
            for candidate in candidates:
                if candidate.is_symlink():
                    continue
                try:
                    candidate.resolve(strict=True).relative_to(self.root)
                except ValueError, OSError, RuntimeError:
                    continue
                virtual = self.virtual_path(candidate)
                if candidate.is_dir():
                    virtual += "/"
                matches.append(virtual)
                if len(matches) >= self.max_results:
                    break
        except (OSError, ValueError) as exc:
            raise WorkspaceFilesystemError(f"Invalid glob pattern: {pattern}") from exc
        return sorted(matches, key=str.casefold)

    def grep(
        self,
        pattern: str,
        *,
        path: str = "/",
        include: str | None = None,
    ) -> list[GrepMatch]:
        """Search UTF-8 workspace files for a literal string."""

        if not pattern:
            raise WorkspaceFilesystemError("Search pattern must not be empty")
        if include is not None:
            self._validate_pattern(include)

        base = self.resolve(path)
        if not base.exists():
            return []
        candidates = (
            [base]
            if base.is_file()
            else sorted(
                base.rglob("*"),
                key=lambda candidate: candidate.as_posix().casefold(),
            )
        )
        matches: list[GrepMatch] = []
        try:
            for candidate in candidates:
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                try:
                    candidate.resolve(strict=True).relative_to(self.root)
                    relative = (
                        candidate.name
                        if base.is_file()
                        else candidate.relative_to(base).as_posix()
                    )
                    if include and not fnmatch.fnmatch(relative, include):
                        continue
                    if candidate.stat().st_size > self.max_file_bytes:
                        continue
                    with candidate.open(encoding="utf-8") as file:
                        for line_number, line in enumerate(file, start=1):
                            if pattern not in line:
                                continue
                            matches.append(
                                {
                                    "path": self.virtual_path(candidate),
                                    "line": line_number,
                                    "text": line.rstrip("\r\n"),
                                }
                            )
                            if len(matches) >= self.max_results:
                                return matches
                except UnicodeDecodeError, OSError, ValueError:
                    continue
        except OSError as exc:
            raise WorkspaceFilesystemError(f"Could not search path: {path}") from exc
        return matches

    def _regular_file(self, path: str) -> Path:
        target = self.resolve(path)
        if not target.exists():
            raise WorkspaceFilesystemError(f"File not found: {path}")
        if not target.is_file():
            raise WorkspaceFilesystemError(f"Not a regular file: {path}")
        return target

    @staticmethod
    def _validate_pattern(pattern: str) -> None:
        if not pattern or "\0" in pattern:
            raise WorkspaceFilesystemError("Invalid glob pattern")
        parts = PurePosixPath(pattern).parts
        if pattern.startswith(("/", "~")) or ".." in parts:
            raise WorkspaceFilesystemError("Unsafe glob pattern")

    @staticmethod
    def _normalize_virtual(path: str) -> str:
        normalized = PurePosixPath(f"/{path.lstrip('/')}").as_posix()
        return normalized if normalized.startswith("/") else f"/{normalized}"
