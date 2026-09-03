"""Workspace-confined view over a sandbox backend. / 沙箱后端之上的工作区受限视图。"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Final

from zharness.sandbox.protocol import BackendProtocol, FileInfo, GrepMatch
from zharness.skills.constants import DEFAULT_SKILLS_CONTAINER_PATH

_CONTAINER_WORKSPACE: Final = PurePosixPath("/workspace")
_SKILLS_CONTAINER_ROOT: Final = PurePosixPath(DEFAULT_SKILLS_CONTAINER_PATH)


class SandboxWorkspaceError(ValueError):
    """Raised when a workspace path or sandbox file operation is invalid. / 当工作区路径或沙箱文件操作无效时抛出。"""


class SandboxWorkspace:
    """Expose one sandbox backend through a stable workspace namespace.

    ``/workspace`` is the public path contract shared by agents, file tools,
    and command execution. Workspace inputs must use that absolute namespace;
    tool output always uses the same canonical paths.

    ``/workspace`` 是 Agent、文件工具和命令执行共享的公开路径契约。
    工作区输入必须使用该绝对命名空间，工具输出始终使用同样的标准路径。
    """

    def __init__(self, backend: BackendProtocol) -> None:
        self.backend = backend

    @staticmethod
    def _container_path(
        path: str,
        *,
        allow_root: bool = True,
        allow_skills: bool = True,
    ) -> str:
        if not isinstance(path, str) or "\0" in path:
            raise SandboxWorkspaceError("Invalid workspace path")
        if path.startswith("~"):
            raise SandboxWorkspaceError("Home expansion is not allowed")

        candidate = PurePosixPath(path)
        if ".." in candidate.parts:
            raise SandboxWorkspaceError("Path traversal is not allowed")
        if not candidate.is_absolute():
            raise SandboxWorkspaceError("Workspace paths must be absolute")

        # The read-only skills namespace is passed through as an absolute
        # container path; the backends confine it to the skills mount.
        #
        # 只读技能命名空间以绝对容器路径原样传递；后端会将其限制在技能挂载点内。
        if allow_skills and (
            candidate == _SKILLS_CONTAINER_ROOT
            or _SKILLS_CONTAINER_ROOT in candidate.parents
        ):
            return str(candidate)

        if (
            candidate != _CONTAINER_WORKSPACE
            and _CONTAINER_WORKSPACE not in candidate.parents
        ):
            raise SandboxWorkspaceError("Path must be under /workspace")
        if not allow_root and candidate == _CONTAINER_WORKSPACE:
            raise SandboxWorkspaceError("Cannot operate on the workspace root")
        return str(candidate)

    @classmethod
    def command_cwd(cls, path: str = "/workspace") -> str:
        """Map a virtual command cwd into the backend workspace.

        将命令的虚拟 cwd 映射到后端工作区。
        """
        return cls._container_path(path, allow_skills=False)

    @classmethod
    def canonical_path(cls, path: str) -> str:
        """Return the canonical public path for one workspace input. / 返回工作区输入的标准公开路径。"""
        return cls._container_path(path)

    @staticmethod
    def _virtual_path(path: str, *, is_dir: bool = False) -> str:
        candidate = PurePosixPath(path)
        if (
            candidate == _SKILLS_CONTAINER_ROOT
            or _SKILLS_CONTAINER_ROOT in candidate.parents
        ):
            virtual = candidate.as_posix()
        else:
            try:
                candidate.relative_to(_CONTAINER_WORKSPACE)
            except ValueError as exc:
                raise SandboxWorkspaceError(
                    "Sandbox returned a path outside the workspace"
                ) from exc

            virtual = candidate.as_posix()
        if is_dir and virtual != str(_CONTAINER_WORKSPACE):
            virtual += "/"
        return virtual

    @staticmethod
    def _raise_for_error(error: str | None) -> None:
        if error is None:
            return
        message = error.removeprefix("Error: ")
        raise SandboxWorkspaceError(message)

    def ls(self, path: str = "/workspace") -> list[FileInfo]:
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

    def glob(self, pattern: str, *, path: str = "/workspace") -> list[str]:
        result = self.backend.glob(pattern, path=self._container_path(path))
        self._raise_for_error(result.error)
        if result.matches is None:
            raise SandboxWorkspaceError("Sandbox returned no glob matches")
        return [self._virtual_path(match["path"]) for match in result.matches]

    def grep(
        self,
        pattern: str,
        *,
        path: str = "/workspace",
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
