"""Workspace-confined view over a sandbox backend."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final

from zharness.sandbox.protocol import BackendProtocol, FileInfo, GrepMatch

_CONTAINER_WORKSPACE: Final = PurePosixPath("/workspace")


class SandboxWorkspaceError(ValueError):
    """Raised when a workspace path or sandbox file operation is invalid."""


class SandboxWorkspace:
    """Expose one sandbox backend through a virtual workspace root.

    Agent-visible paths are rooted at ``/``. The wrapped sandbox receives only
    paths beneath ``/workspace``, which is the thread workspace mount managed
    by :class:`DockerSandboxManager`.
    """

    def __init__(self, backend: BackendProtocol) -> None:
        self.backend = backend

    @staticmethod
    def _container_path(path: str, *, allow_root: bool = True) -> str:
        if not isinstance(path, str) or "\0" in path:
            raise SandboxWorkspaceError("Invalid workspace path")
        if path.startswith("~"):
            raise SandboxWorkspaceError("Home expansion is not allowed")

        candidate = PurePosixPath(path or "/")
        if ".." in candidate.parts:
            raise SandboxWorkspaceError("Path traversal is not allowed")

        relative_parts = (
            candidate.parts[1:] if candidate.is_absolute() else candidate.parts
        )
        mapped = _CONTAINER_WORKSPACE.joinpath(*relative_parts)
        if not allow_root and mapped == _CONTAINER_WORKSPACE:
            raise SandboxWorkspaceError("Cannot operate on the workspace root")
        return str(mapped)

    @staticmethod
    def _virtual_path(path: str, *, is_dir: bool = False) -> str:
        candidate = PurePosixPath(path)
        try:
            relative = candidate.relative_to(_CONTAINER_WORKSPACE)
        except ValueError as exc:
            raise SandboxWorkspaceError(
                "Sandbox returned a path outside the workspace"
            ) from exc

        virtual = "/" if relative == PurePosixPath(".") else f"/{relative}"
        if is_dir and virtual != "/":
            virtual += "/"
        return virtual

    @staticmethod
    def _raise_for_error(error: str | None) -> None:
        if error is None:
            return
        message = error.removeprefix("Error: ")
        raise SandboxWorkspaceError(message)

    def ls(self, path: str = "/") -> list[FileInfo]:
        result = self.backend.ls(self._container_path(path))
        self._raise_for_error(result.error)
        if result.entries is None:
            raise SandboxWorkspaceError("Sandbox returned no directory listing")

        entries: list[FileInfo] = []
        for entry in result.entries:
            mapped = dict(entry)
            mapped["path"] = self._virtual_path(
                entry["path"], is_dir=entry.get("is_dir", False)
            )
            entries.append(mapped)  # type: ignore[arg-type]
        return entries

    def read(self, path: str, *, offset: int = 0, limit: int = 2000) -> str:
        result = self.backend.read(
            self._container_path(path), offset=offset, limit=limit
        )
        self._raise_for_error(result.error)
        if result.file_data is None:
            raise SandboxWorkspaceError("Sandbox returned no file data")
        if result.file_data["encoding"] != "utf-8":
            raise SandboxWorkspaceError(f"File is not UTF-8 text: {path}")
        return result.file_data["content"]

    def write(self, path: str, content: str) -> str:
        container_path = self._container_path(path, allow_root=False)
        result = self.backend.write(container_path, content)
        self._raise_for_error(result.error)
        if result.path is None:
            raise SandboxWorkspaceError("Sandbox returned no written path")
        return self._virtual_path(result.path)

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        *,
        replace_all: bool = False,
    ) -> int:
        result = self.backend.edit(
            self._container_path(path, allow_root=False),
            old_string,
            new_string,
            replace_all=replace_all,
        )
        self._raise_for_error(result.error)
        if result.occurrences is None:
            raise SandboxWorkspaceError("Sandbox returned no edit count")
        return result.occurrences

    def delete(self, path: str) -> str:
        try:
            container_path = self._container_path(path, allow_root=False)
        except SandboxWorkspaceError as exc:
            if str(exc) == "Cannot operate on the workspace root":
                raise SandboxWorkspaceError("Cannot delete the workspace root") from exc
            raise
        result = self.backend.delete(container_path)
        self._raise_for_error(result.error)
        if result.path is None:
            raise SandboxWorkspaceError("Sandbox returned no deleted path")
        return self._virtual_path(result.path)

    def glob(self, pattern: str, *, path: str = "/") -> list[str]:
        result = self.backend.glob(pattern, path=self._container_path(path))
        self._raise_for_error(result.error)
        if result.matches is None:
            raise SandboxWorkspaceError("Sandbox returned no glob matches")
        return [self._virtual_path(match["path"]) for match in result.matches]

    def grep(
        self,
        pattern: str,
        *,
        path: str = "/",
        include: str | None = None,
    ) -> list[GrepMatch]:
        result = self.backend.grep(
            pattern,
            path=self._container_path(path),
            glob=include,
        )
        self._raise_for_error(result.error)
        if result.matches is None:
            raise SandboxWorkspaceError("Sandbox returned no grep matches")

        matches: list[GrepMatch] = []
        for match in result.matches:
            mapped = dict(match)
            mapped["path"] = self._virtual_path(match["path"])
            matches.append(mapped)  # type: ignore[arg-type]
        return matches
