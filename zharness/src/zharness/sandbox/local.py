"""Local filesystem sandbox backend.

[`LocalSandbox`][zharness.sandbox.local.LocalSandbox] implements
[`SandboxBackendProtocol`][zharness.sandbox.protocol.SandboxBackendProtocol]
against a directory on the host. It is the analogue of the Docker backend for
single-user, trusted local deployments: file tools operate directly on a local
root, and host bash execution is disabled unless explicitly enabled.

Agent-visible paths are rooted at ``/workspace`` (the same path space the
Docker backend uses) and map onto ``root`` on the host. Path traversal, home
expansion, and symlink escapes are rejected, so every operation stays confined
to ``root``.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

from zharness.host.paths import ensure_thread_workspace
from zharness.sandbox.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    PERMISSION_DENIED,
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from zharness.skills.constants import DEFAULT_SKILLS_CONTAINER_PATH
from zharness.utils import (
    InvalidGlobPatternError,
    compile_grep_include_glob,
    slice_read_response,
)

logger = logging.getLogger(__name__)

SANDBOX_ROOT: Final = PurePosixPath("/workspace")
SKILLS_SANDBOX_ROOT: Final = PurePosixPath(DEFAULT_SKILLS_CONTAINER_PATH)

DEFAULT_MAX_FILE_BYTES: Final = 256 * 1024
DEFAULT_MAX_RESULTS: Final = 100
DEFAULT_MAX_OUTPUT_BYTES: Final = 1024 * 1024
DEFAULT_EXECUTE_TIMEOUT: Final = 30


class LocalSandboxError(ValueError):
    """Raised when a local sandbox operation cannot be completed safely."""


class LocalSandbox(SandboxBackendProtocol):
    """Access a host directory through the sandbox backend protocol."""

    def __init__(
        self,
        root: str | Path,
        *,
        allow_host_bash: bool = False,
        skills_root: str | Path | None = None,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_results: int = DEFAULT_MAX_RESULTS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        if max_results < 1:
            raise ValueError("max_results must be positive")
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")

        try:
            resolved_root = Path(root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise LocalSandboxError("Workspace does not exist") from exc
        if not resolved_root.is_dir():
            raise LocalSandboxError("Workspace root is not a directory")

        self.root = resolved_root
        self.allow_host_bash = allow_host_bash
        self.skills_root: Path | None = None
        if skills_root is not None:
            try:
                resolved_skills = Path(skills_root).expanduser().resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise LocalSandboxError("Skills root does not exist") from exc
            if not resolved_skills.is_dir():
                raise LocalSandboxError("Skills root is not a directory")
            self.skills_root = resolved_skills
        self.max_file_bytes = max_file_bytes
        self.max_results = max_results
        self.max_output_bytes = max_output_bytes
        self._process_lock = threading.Lock()
        self._processes: set[subprocess.Popen[bytes]] = set()

    @property
    def id(self) -> str:
        """Stable identifier for this local sandbox instance."""
        return f"local:{self.root}"

    # ------------------------------------------------------------------
    # Path translation
    # ------------------------------------------------------------------

    @staticmethod
    def _is_skills_path(path: str) -> bool:
        parsed = PurePosixPath(path)
        return parsed == SKILLS_SANDBOX_ROOT or SKILLS_SANDBOX_ROOT in parsed.parents

    def _assert_within_mount(self, resolved: Path) -> None:
        """Raise ``ValueError`` when ``resolved`` escapes every configured root. / 当 ``resolved`` 逃逸所有已配置根目录时抛出 ``ValueError``。"""
        try:
            resolved.relative_to(self.root)
            return
        except ValueError:
            pass
        if self.skills_root is not None:
            try:
                resolved.relative_to(self.skills_root)
                return
            except ValueError:
                pass
        raise ValueError("Path escapes the workspace")

    def _lexical_path(self, path: str, *, allow_root: bool = True) -> Path:
        """Map a validated sandbox path to an unresolved host path."""
        if not isinstance(path, str) or "\0" in path:
            raise LocalSandboxError("Invalid sandbox path")
        if path.startswith("~"):
            raise LocalSandboxError("Home expansion is not allowed")

        parsed = PurePosixPath(path)
        if not parsed.is_absolute():
            raise LocalSandboxError("Sandbox paths must be absolute")

        if self._is_skills_path(path):
            if self.skills_root is None:
                raise LocalSandboxError("Skills mount is not configured")
            host = self.skills_root.joinpath(
                *parsed.relative_to(SKILLS_SANDBOX_ROOT).parts
            )
            if not allow_root and host == self.skills_root:
                raise LocalSandboxError("Cannot operate on the skills mount root")
            return host

        try:
            relative = parsed.relative_to(SANDBOX_ROOT)
        except ValueError as exc:
            raise LocalSandboxError("Path escapes the sandbox workspace") from exc

        host = self.root.joinpath(*relative.parts)
        if not allow_root and host == self.root:
            raise LocalSandboxError("Cannot operate on the workspace root")
        return host

    def resolve_path(self, path: str) -> Path:
        """Resolve a sandbox path, rejecting symlinks that escape the roots. / 解析沙箱路径，并拒绝逃逸根目录的符号链接。"""
        lexical = self._lexical_path(path)
        try:
            resolved = lexical.resolve(strict=False)
            self._assert_within_mount(resolved)
        except ValueError as exc:
            raise LocalSandboxError("Path escapes the workspace") from exc
        except (OSError, RuntimeError) as exc:
            raise LocalSandboxError(f"Invalid workspace path: {path}") from exc
        return resolved

    def _sandbox_path(self, host: Path) -> str:
        """Convert a host path beneath ``root`` or the skills mount to a sandbox path. / 将 ``root`` 或技能挂载点下的主机路径转换为沙箱路径。"""
        if self.skills_root is not None:
            try:
                relative = host.relative_to(self.skills_root)
            except ValueError:
                pass
            else:
                if not relative.parts:
                    return str(SKILLS_SANDBOX_ROOT)
                return f"{SKILLS_SANDBOX_ROOT}/{relative.as_posix()}"
        try:
            relative = host.relative_to(self.root)
        except ValueError as exc:
            raise LocalSandboxError("Path escapes the workspace") from exc
        if not relative.parts:
            return str(SANDBOX_ROOT)
        return f"{SANDBOX_ROOT}/{relative.as_posix()}"

    @staticmethod
    def _normalize_virtual(path: str) -> str:
        normalized = PurePosixPath(path).as_posix()
        return str(normalized)

    def _regular_file(self, path: str) -> Path:
        target = self.resolve_path(path)
        if not target.exists():
            raise LocalSandboxError(f"File not found: {path}")
        if not target.is_file():
            raise LocalSandboxError(f"Not a regular file: {path}")
        return target

    def _host_command(
        self,
        command: str,
        *,
        skills_root: Path | None = None,
    ) -> str:
        """Map the advertised skills mount to its host path for local execution. / 为本地执行将对外提供的技能挂载路径映射到其主机路径。"""
        if skills_root is None:
            return command

        mount = str(SKILLS_SANDBOX_ROOT)
        host = str(skills_root)
        mapped: list[str] = []
        quote: str | None = None
        escaped = False
        index = 0
        while index < len(command):
            char = command[index]
            if escaped:
                mapped.append(char)
                escaped = False
                index += 1
                continue
            if char == "\\" and quote != "'":
                mapped.append(char)
                escaped = True
                index += 1
                continue
            if char in {"'", '"'}:
                if quote is None:
                    quote = char
                elif quote == char:
                    quote = None
                mapped.append(char)
                index += 1
                continue
            if command.startswith(mount, index) and (
                index + len(mount) == len(command) or command[index + len(mount)] == "/"
            ):
                if quote == "'":
                    mapped.append(host.replace("'", "'\"'\"'"))
                elif quote == '"':
                    mapped.append(
                        host.replace("\\", "\\\\")
                        .replace('"', '\\"')
                        .replace("$", "\\$")
                        .replace("`", "\\`")
                    )
                else:
                    mapped.append(shlex.quote(host))
                index += len(mount)
                continue
            mapped.append(char)
            index += 1
        return "".join(mapped)

    @contextmanager
    def _command_skills_snapshot(self) -> Iterator[Path | None]:
        """Expose disposable skill copies to host commands. / 向主机命令公开一次性的技能副本。"""
        if self.skills_root is None:
            yield None
            return

        def ignore_symlinks(directory: str, names: list[str]) -> list[str]:
            """Exclude links so a snapshot cannot point back to host data. / 排除链接，避免快照重新指向主机数据。"""
            return [name for name in names if (Path(directory) / name).is_symlink()]

        with tempfile.TemporaryDirectory(prefix="zharness-skills-") as temp_dir:
            snapshot = Path(temp_dir) / "skills"
            shutil.copytree(self.skills_root, snapshot, ignore=ignore_symlinks)
            yield snapshot

    # ------------------------------------------------------------------
    # SandboxBackendProtocol operations
    # ------------------------------------------------------------------

    def ls(self, path: str) -> LsResult:
        """List direct children of a sandbox directory in deterministic order."""
        try:
            directory = self.resolve_path(path)
            if not directory.exists():
                return LsResult(error=f"Path not found: {path}")
            if not directory.is_dir():
                return LsResult(error=f"Not a directory: {path}")

            entries: list[dict] = []
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
                    self._assert_within_mount(resolved)
                    stat = child.stat()
                except (ValueError, OSError, RuntimeError):
                    continue
                is_dir = child.is_dir()
                if not is_dir and not child.is_file():
                    continue
                virtual = self._sandbox_path(child)
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
            return LsResult(entries=entries)
        except LocalSandboxError as exc:
            return LsResult(error=str(exc))
        except OSError:
            return LsResult(error=f"Could not list directory: {path}")

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Read a UTF-8 file, optionally selecting a zero-based line window."""
        try:
            target = self._regular_file(file_path)
        except LocalSandboxError as exc:
            return ReadResult(error=str(exc))
        try:
            if target.stat().st_size > self.max_file_bytes:
                return ReadResult(
                    error=f"File exceeds the {self.max_file_bytes}-byte limit: {file_path}"
                )
            descriptor = os.open(
                target,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as file:
                content = file.read(self.max_file_bytes + 1)
        except OSError:
            return ReadResult(error=f"Could not read file: {file_path}")

        if len(content) > self.max_file_bytes:
            return ReadResult(
                error=f"File exceeds the {self.max_file_bytes}-byte limit: {file_path}"
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return ReadResult(error=f"File is not UTF-8 text: {file_path}")

        return slice_read_response(
            {"content": text, "encoding": "utf-8"},
            offset,
            limit,
        )

    def write(self, file_path: str, content: str) -> WriteResult:
        """Atomically create or replace a UTF-8 text file."""
        if self._is_skills_path(file_path):
            return WriteResult(error="The skills mount is read-only")
        if not isinstance(content, str):
            return WriteResult(error="File content must be text")
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            return WriteResult(
                error=f"Content exceeds the {self.max_file_bytes}-byte limit"
            )

        try:
            lexical = self._lexical_path(file_path, allow_root=False)
        except LocalSandboxError as exc:
            return WriteResult(error=str(exc))
        if lexical == self.root:
            return WriteResult(error="Cannot write to the workspace root")

        try:
            self.resolve_path(file_path)
            lexical.parent.mkdir(parents=True, exist_ok=True)
            parent = lexical.parent.resolve(strict=True)
            parent.relative_to(self.root)
            target = parent / lexical.name
            if target.is_symlink():
                return WriteResult(error=f"Cannot overwrite symlink: {file_path}")
            if target.exists() and not target.is_file():
                return WriteResult(error=f"Not a regular file: {file_path}")
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
        except LocalSandboxError as exc:
            return WriteResult(error=str(exc))
        except ValueError as exc:
            return WriteResult(error=f"Path escapes the workspace: {exc}")
        except OSError:
            return WriteResult(error=f"Could not write file: {file_path}")

        return WriteResult(path=self._normalize_virtual(file_path))

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Replace one unique string, or every occurrence, in a text file."""
        if not old_string:
            return EditResult(error="old_string must not be empty")
        read_result = self.read(file_path, limit=2**31 - 1)
        if read_result.error is not None or read_result.file_data is None:
            return EditResult(error=read_result.error)
        content = read_result.file_data["content"]
        occurrences = content.count(old_string)
        if occurrences == 0:
            return EditResult(error="old_string was not found")
        if not replace_all and occurrences != 1:
            return EditResult(
                error=f"old_string is not unique ({occurrences} occurrences)"
            )
        updated = content.replace(old_string, new_string, -1 if replace_all else 1)
        write_result = self.write(file_path, updated)
        if write_result.error is not None:
            return EditResult(error=write_result.error)
        return EditResult(
            path=write_result.path,
            occurrences=(occurrences if replace_all else 1),
        )

    def delete(self, file_path: str) -> DeleteResult:
        """Delete a file, symlink, or directory tree without following links."""
        if self._is_skills_path(file_path):
            return DeleteResult(error="The skills mount is read-only")
        try:
            lexical = self._lexical_path(file_path, allow_root=False)
        except LocalSandboxError as exc:
            return DeleteResult(error=str(exc))
        if lexical == self.root:
            return DeleteResult(error="Cannot delete the workspace root")
        try:
            lexical.parent.resolve(strict=True).relative_to(self.root)
        except ValueError as exc:
            return DeleteResult(error=f"Path escapes the workspace: {exc}")
        except (OSError, RuntimeError):
            return DeleteResult(error=f"Invalid workspace path: {file_path}")

        if lexical.is_symlink():
            try:
                lexical.unlink()
            except OSError:
                return DeleteResult(error=f"Could not delete: {file_path}")
            return DeleteResult(path=self._normalize_virtual(file_path))

        try:
            target = self.resolve_path(file_path)
        except LocalSandboxError as exc:
            return DeleteResult(error=str(exc))
        if not target.exists():
            return DeleteResult(error=f"Path not found: {file_path}")
        try:
            if not target.is_dir():
                target.unlink()
            else:
                shutil.rmtree(target)
        except OSError:
            return DeleteResult(error=f"Could not delete: {file_path}")
        return DeleteResult(path=self._normalize_virtual(file_path))

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Find files matching a glob pattern beneath a sandbox directory."""
        try:
            matcher = compile_grep_include_glob(pattern)
            base = self.resolve_path(path or str(SANDBOX_ROOT))
        except (InvalidGlobPatternError, LocalSandboxError) as exc:
            return GlobResult(error=str(exc))
        if not base.exists():
            return GlobResult(matches=[])
        if not base.is_dir():
            return GlobResult(error=f"Not a directory: {path}")

        matches: list[dict] = []
        try:
            candidates = sorted(base.rglob("*"), key=lambda c: c.as_posix().casefold())
            for candidate in candidates:
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                try:
                    self._assert_within_mount(candidate.resolve(strict=True))
                except (ValueError, OSError, RuntimeError):
                    continue
                relative = candidate.relative_to(base).as_posix()
                if not matcher(relative):
                    continue
                if len(matches) >= self.max_results:
                    return GlobResult(
                        matches=matches, truncated=True, truncation_reason="budget"
                    )
                matches.append({"path": self._sandbox_path(candidate)})
        except (OSError, ValueError):
            return GlobResult(error=f"Invalid glob pattern: {pattern}")
        return GlobResult(matches=matches)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Search UTF-8 sandbox files for a literal string."""
        if not pattern:
            return GrepResult(error="Search pattern must not be empty")
        matcher = None
        if glob is not None:
            try:
                matcher = compile_grep_include_glob(glob)
            except InvalidGlobPatternError as exc:
                return GrepResult(error=str(exc))
        try:
            base = self.resolve_path(path or str(SANDBOX_ROOT))
        except LocalSandboxError as exc:
            return GrepResult(error=str(exc))
        if not base.exists():
            return GrepResult(matches=[])

        candidates = (
            [base]
            if base.is_file()
            else sorted(base.rglob("*"), key=lambda c: c.as_posix().casefold())
        )
        matches: list[GrepMatch] = []
        try:
            for candidate in candidates:
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                try:
                    self._assert_within_mount(candidate.resolve(strict=True))
                    relative = (
                        candidate.name
                        if base.is_file()
                        else candidate.relative_to(base).as_posix()
                    )
                    if matcher is not None and not matcher(relative):
                        continue
                    if candidate.stat().st_size > self.max_file_bytes:
                        continue
                    with candidate.open(encoding="utf-8") as file:
                        for line_number, line in enumerate(file, start=1):
                            if pattern not in line:
                                continue
                            if max_count is not None and len(matches) >= max_count:
                                return GrepResult(matches=matches, truncated=True)
                            if len(matches) >= self.max_results:
                                return GrepResult(matches=matches, truncated=True)
                            matches.append(
                                {
                                    "path": self._sandbox_path(candidate),
                                    "line": line_number,
                                    "text": line.rstrip("\r\n"),
                                }
                            )
                except (UnicodeDecodeError, OSError, ValueError):
                    continue
        except OSError:
            return GrepResult(error=f"Could not search path: {path}")
        return GrepResult(matches=matches)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Write byte content directly to files beneath ``root``."""
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                if not isinstance(content, bytes):
                    raise TypeError("file content must be bytes")
                if self._is_skills_path(path):
                    raise LocalSandboxError("The skills mount is read-only")
                lexical = self._lexical_path(path)
                if lexical == self.root:
                    raise LocalSandboxError("Cannot write to the workspace root")
                if len(content) > self.max_file_bytes:
                    responses.append(
                        FileUploadResponse(
                            path=path,
                            error=f"file exceeds {self.max_file_bytes}-byte limit",
                        )
                    )
                    continue
                self.resolve_path(path)
                lexical.parent.mkdir(parents=True, exist_ok=True)
                parent = lexical.parent.resolve(strict=True)
                parent.relative_to(self.root)
                target = parent / lexical.name
                if target.is_symlink():
                    raise LocalSandboxError("Cannot overwrite symlink")
                descriptor, temporary_name = tempfile.mkstemp(
                    dir=target.parent,
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                )
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "wb") as file:
                        file.write(content)
                        file.flush()
                        os.fsync(file.fileno())
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
                responses.append(FileUploadResponse(path=path))
            except LocalSandboxError as exc:
                responses.append(
                    FileUploadResponse(path=path, error=f"{INVALID_PATH}: {exc}")
                )
            except PermissionError as exc:
                responses.append(
                    FileUploadResponse(path=path, error=f"{PERMISSION_DENIED}: {exc}")
                )
            except (TypeError, OSError, ValueError) as exc:
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Read byte content from files beneath ``root``."""
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                target = self.resolve_path(path)
                if not target.exists():
                    responses.append(
                        FileDownloadResponse(path=path, error=FILE_NOT_FOUND)
                    )
                    continue
                if target.is_dir():
                    responses.append(
                        FileDownloadResponse(path=path, error=IS_DIRECTORY)
                    )
                    continue
                if not target.is_file():
                    responses.append(
                        FileDownloadResponse(path=path, error="not a regular file")
                    )
                    continue
                if target.stat().st_size > self.max_file_bytes:
                    responses.append(
                        FileDownloadResponse(
                            path=path,
                            error=f"file exceeds {self.max_file_bytes}-byte limit",
                        )
                    )
                    continue
                descriptor = os.open(
                    target,
                    os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
                )
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    os.close(descriptor)
                    responses.append(
                        FileDownloadResponse(path=path, error="not a regular file")
                    )
                    continue
                with os.fdopen(descriptor, "rb") as file:
                    content = file.read(self.max_file_bytes + 1)
                if len(content) > self.max_file_bytes:
                    responses.append(
                        FileDownloadResponse(
                            path=path, error="file exceeds transfer limit"
                        )
                    )
                    continue
                responses.append(FileDownloadResponse(path=path, content=content))
            except LocalSandboxError as exc:
                responses.append(FileDownloadResponse(path=path, error=str(exc)))
            except OSError as exc:
                responses.append(
                    FileDownloadResponse(path=path, error=f"{PERMISSION_DENIED}: {exc}")
                )
        return responses

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Run a shell command on the host when host bash is enabled. / 启用主机 Bash 后在主机上运行 shell 命令。"""
        if not self.allow_host_bash:
            return ExecuteResponse(
                output=(
                    "Host bash execution is disabled for the local sandbox. "
                    "Set ZHARNESS_ALLOW_HOST_BASH=1 to enable it."
                ),
                exit_code=1,
            )
        if not isinstance(command, str) or not command:
            return ExecuteResponse(
                output="Error: command must not be empty", exit_code=2
            )
        if timeout is not None and (isinstance(timeout, bool) or timeout < 0):
            return ExecuteResponse(
                output="Error: timeout must be a non-negative integer", exit_code=2
            )

        output = bytearray()
        truncated = False
        reader_errors: list[OSError] = []

        try:
            with self._command_skills_snapshot() as skills_snapshot:
                proc = subprocess.Popen(
                    [
                        "/bin/sh",
                        "-lc",
                        self._host_command(command, skills_root=skills_snapshot),
                    ],
                    cwd=self.root,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                with self._process_lock:
                    self._processes.add(proc)

                def drain_output() -> None:
                    """Retain only the configured prefix while draining stdout. / 排空标准输出，同时仅保留配置的前缀。"""
                    nonlocal truncated
                    assert proc.stdout is not None
                    try:
                        while chunk := proc.stdout.read(64 * 1024):
                            remaining = self.max_output_bytes - len(output)
                            if remaining > 0:
                                output.extend(chunk[:remaining])
                            if len(chunk) > remaining:
                                truncated = True
                    except OSError as exc:
                        reader_errors.append(exc)

                reader = threading.Thread(target=drain_output, daemon=True)
                reader.start()
                timed_out = False
                try:
                    proc.wait(timeout=(timeout if timeout else DEFAULT_EXECUTE_TIMEOUT))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._terminate_process_group(proc)
                    proc.wait()
                finally:
                    reader.join(timeout=1)
                    if reader.is_alive():
                        # A descendant inherited stdout after the shell exited. / shell 退出后仍有子进程继承了标准输出。
                        self._terminate_process_group(proc)
                        reader.join(timeout=1)
                    if reader.is_alive() and proc.stdout is not None:
                        proc.stdout.close()
                        reader.join()
                    with self._process_lock:
                        self._processes.discard(proc)

                if timed_out:
                    return ExecuteResponse(
                        output="Command timed out", exit_code=124, truncated=True
                    )
        except OSError as exc:
            return ExecuteResponse(output=f"Local execution failed: {exc}", exit_code=1)

        if reader_errors:
            return ExecuteResponse(
                output=f"Local execution failed: {reader_errors[0]}", exit_code=1
            )
        return ExecuteResponse(
            output=bytes(output).decode("utf-8", errors="replace"),
            exit_code=proc.returncode,
            truncated=truncated,
        )

    @staticmethod
    def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
        """Kill a command and every descendant that remains in its process group."""
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            proc.kill()

    def stop_processes(self) -> bool:
        """Stop commands currently running in this sandbox."""
        with self._process_lock:
            processes = tuple(self._processes)
        for proc in processes:
            self._terminate_process_group(proc)
        return bool(processes)


@dataclass(frozen=True, slots=True)
class LocalSandboxSettings:
    """Configuration for the local sandbox provider. / 本地沙箱提供程序的配置。"""

    root: str | None = None
    allow_host_bash: bool = False
    skills_root: str | None = None

    @classmethod
    def from_env(cls) -> LocalSandboxSettings:
        """Build settings from environment variables. / 从环境变量构建配置。"""
        return cls(
            root=os.environ.get("ZHARNESS_LOCAL_ROOT"),
            allow_host_bash=os.environ.get("ZHARNESS_ALLOW_HOST_BASH", "").lower()
            in {"1", "true", "yes"},
            skills_root=_env_skills_root(),
        )


def _env_skills_root() -> str | None:
    """Resolve the configured skills directory for a local sandbox, if any. / 解析本地沙箱已配置的技能目录（如有）。"""
    from zharness.skills.storage import skills_root_path

    try:
        root = skills_root_path()
    except Exception:
        logger.exception("Failed to resolve skills root for local sandbox")
        return None
    return str(root) if root.is_dir() else None


class LocalSandboxManager:
    """Provide one `LocalSandbox` per server thread.

    Without a configured root, each thread gets its own workspace under
    ``ZHARNESS_HOME/workspaces`` (mirroring the Docker backend). When
    ``ZHARNESS_LOCAL_ROOT`` is set, every thread shares that directory, which
    is the intended way to let the agent work directly on a local project.

    为每个服务器线程提供一个 `LocalSandbox`。

    未配置根目录时，每个线程会在 ``ZHARNESS_HOME/workspaces`` 下获得独立工作区
    （与 Docker 后端一致）。设置 ``ZHARNESS_LOCAL_ROOT`` 后，所有线程共享该目录，
    这是让 agent 直接处理本地项目的预期方式。
    """

    def __init__(
        self,
        *,
        client: object | None = None,
        settings: LocalSandboxSettings | None = None,
    ) -> None:
        del client  # Accepted for a common manager interface; unused locally. / 为统一管理器接口而接受；本地未使用。
        self.settings = settings or LocalSandboxSettings.from_env()
        self._lock = threading.Lock()
        self._sandboxes: dict[str, LocalSandbox] = {}

    def for_thread(self, thread_id: str) -> LocalSandbox:
        """Return the local sandbox for a thread, creating it on first use. / 返回线程的本地沙箱，首次使用时创建。"""
        with self._lock:
            sandbox = self._sandboxes.get(thread_id)
            if sandbox is None:
                if self.settings.root:
                    root = Path(self.settings.root).expanduser().resolve(strict=False)
                    root.mkdir(parents=True, exist_ok=True)
                else:
                    root = ensure_thread_workspace(thread_id)
                sandbox = LocalSandbox(
                    root,
                    allow_host_bash=self.settings.allow_host_bash,
                    skills_root=self.settings.skills_root,
                )
                self._sandboxes[thread_id] = sandbox
            return sandbox

    def remove_for_thread(self, thread_id: str) -> bool:
        """Drop a thread's local sandbox, removing its per-thread workspace. / 移除线程的本地沙箱及其线程工作区。"""
        with self._lock:
            sandbox = self._sandboxes.pop(thread_id, None)
        if sandbox is None:
            return False
        sandbox.stop_processes()
        if self.settings.root is None:
            try:
                shutil.rmtree(sandbox.root)
            except OSError:
                logger.exception("Failed to remove workspace for thread %s", thread_id)
                return False
        return True

    def stop_all(self, *, timeout: int = 10) -> list[str]:
        """Stop commands currently running in any local sandbox. / 停止所有本地沙箱中当前运行的命令。"""
        del timeout
        with self._lock:
            sandboxes = tuple(self._sandboxes.values())
        return [sandbox.id for sandbox in sandboxes if sandbox.stop_processes()]
