"""Docker implementation of the sandbox backend."""

from __future__ import annotations

import io
import shlex
import tarfile
from pathlib import PurePosixPath
from typing import Any, Final

from docker.errors import APIError, DockerException

from zharness.sandbox.protocol import (
    FILE_NOT_FOUND,
    INVALID_PATH,
    IS_DIRECTORY,
    PERMISSION_DENIED,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from zharness.sandbox.sandbox import BaseSandbox

DEFAULT_MAX_OUTPUT_BYTES: Final = 1024 * 1024
DEFAULT_MAX_TRANSFER_BYTES: Final = 16 * 1024 * 1024


def _validated_path(path: str) -> PurePosixPath:
    """Validate an absolute path passed across the container boundary."""

    if not isinstance(path, str) or not path.startswith("/") or "\0" in path:
        raise ValueError("sandbox paths must be absolute")
    parsed = PurePosixPath(path)
    if ".." in parsed.parts or parsed == PurePosixPath("/"):
        raise ValueError("invalid sandbox path")
    return parsed


def _api_error_code(exc: APIError) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code == 404:
        return FILE_NOT_FOUND
    if status_code in {401, 403}:
        return PERMISSION_DENIED
    return str(exc)


class DockerSandbox(BaseSandbox):
    """Run commands and transfer files through one Docker container."""

    enable_capture_offload = True

    def __init__(
        self,
        container: Any,
        *,
        workdir: str = "/workspace",
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        max_transfer_bytes: int = DEFAULT_MAX_TRANSFER_BYTES,
    ) -> None:
        if max_output_bytes < 1 or max_transfer_bytes < 1:
            raise ValueError("sandbox byte limits must be positive")
        self.container = container
        self.workdir = workdir
        self.max_output_bytes = max_output_bytes
        self.max_transfer_bytes = max_transfer_bytes
        self._ownership: tuple[int, int] | None = None

    @property
    def id(self) -> str:
        return str(self.container.id)

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command, bounding its runtime and retained output."""

        if not isinstance(command, str) or not command:
            return ExecuteResponse(
                output="Error: command must not be empty", exit_code=2
            )
        if timeout is not None and (isinstance(timeout, bool) or timeout < 0):
            return ExecuteResponse(
                output="Error: timeout must be a non-negative integer", exit_code=2
            )

        cmd = ["/bin/sh", "-lc", command]
        if timeout:
            cmd = [
                "timeout",
                "--signal=TERM",
                "--kill-after=1s",
                f"{timeout}s",
                *cmd,
            ]

        try:
            api = self.container.client.api
            created = api.exec_create(
                self.container.id,
                cmd,
                stdout=True,
                stderr=True,
                workdir=self.workdir,
            )
            exec_id = created["Id"]
            stream = api.exec_start(exec_id, stream=True, demux=False)

            output = bytearray()
            truncated = False
            for chunk in stream:
                if not chunk:
                    continue
                remaining = self.max_output_bytes - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True

            details = api.exec_inspect(exec_id)
            return ExecuteResponse(
                output=bytes(output).decode("utf-8", errors="replace"),
                exit_code=details.get("ExitCode"),
                truncated=truncated,
            )
        except DockerException as exc:
            return ExecuteResponse(output=f"Docker execution failed: {exc}")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                target = _validated_path(path)
            except ValueError as exc:
                responses.append(
                    FileUploadResponse(path=path, error=f"{INVALID_PATH}: {exc}")
                )
                continue

            try:
                if not isinstance(content, bytes):
                    raise TypeError("file content must be bytes")
                if len(content) > self.max_transfer_bytes:
                    responses.append(
                        FileUploadResponse(
                            path=path,
                            error=f"file exceeds {self.max_transfer_bytes}-byte transfer limit",
                        )
                    )
                    continue

                parent = str(target.parent)
                mkdir = self.execute(f"mkdir -p -- {shlex.quote(parent)}")
                if mkdir.exit_code != 0:
                    error = (
                        PERMISSION_DENIED
                        if "Permission denied" in mkdir.output
                        else mkdir.output or "could not create parent directory"
                    )
                    responses.append(FileUploadResponse(path=path, error=error))
                    continue

                archive = io.BytesIO()
                with tarfile.open(fileobj=archive, mode="w") as tar:
                    info = tarfile.TarInfo(target.name)
                    info.size = len(content)
                    info.mode = 0o600
                    info.uid, info.gid = self._container_ownership()
                    info.uname = ""
                    info.gname = ""
                    tar.addfile(info, io.BytesIO(content))
                archive.seek(0)
                if not self.container.put_archive(parent, archive.read()):
                    raise DockerException("Docker rejected the archive")
                responses.append(FileUploadResponse(path=path))
            except APIError as exc:
                responses.append(
                    FileUploadResponse(path=path, error=_api_error_code(exc))
                )
            except DockerException as exc:
                responses.append(FileUploadResponse(path=path, error=str(exc)))
            except Exception as exc:  # noqa: BLE001 - batch contract requires per-file errors
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def _container_ownership(self) -> tuple[int, int]:
        """Return the numeric user/group used by commands in the container."""

        if self._ownership is not None:
            return self._ownership

        configured = str(
            self.container.attrs.get("Config", {}).get("User", "")
        ).partition(":")
        user, _, group = configured
        if user.isdigit() and (not group or group.isdigit()):
            uid = int(user)
            self._ownership = (uid, int(group) if group else uid)
            return self._ownership

        result = self.execute("id -u && id -g")
        values = result.output.splitlines()
        if (
            result.exit_code != 0
            or len(values) != 2
            or not all(value.isdigit() for value in values)
        ):
            raise DockerException("could not determine the sandbox user")
        self._ownership = (int(values[0]), int(values[1]))
        return self._ownership

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                target = _validated_path(path)
            except ValueError as exc:
                responses.append(
                    FileDownloadResponse(path=path, error=f"{INVALID_PATH}: {exc}")
                )
                continue

            try:
                chunks, metadata = self.container.get_archive(str(target))
                size = int(metadata.get("size", 0))
                if size > self.max_transfer_bytes:
                    responses.append(
                        FileDownloadResponse(
                            path=path,
                            error=f"file exceeds {self.max_transfer_bytes}-byte transfer limit",
                        )
                    )
                    continue

                archive_bytes = bytearray()
                archive_limit = self.max_transfer_bytes + (2 * 1024 * 1024)
                for chunk in chunks:
                    archive_bytes.extend(chunk)
                    if len(archive_bytes) > archive_limit:
                        raise ValueError("Docker archive exceeds transfer limit")

                with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as tar:
                    members = tar.getmembers()
                    if members and members[0].isdir():
                        responses.append(
                            FileDownloadResponse(path=path, error=IS_DIRECTORY)
                        )
                        continue
                    if len(members) != 1 or not members[0].isfile():
                        raise ValueError("unexpected Docker archive contents")
                    extracted = tar.extractfile(members[0])
                    if extracted is None:
                        raise ValueError("Docker archive did not contain a file")
                    content = extracted.read(self.max_transfer_bytes + 1)
                if len(content) > self.max_transfer_bytes:
                    raise ValueError("file exceeds transfer limit")
                responses.append(FileDownloadResponse(path=path, content=content))
            except APIError as exc:
                responses.append(
                    FileDownloadResponse(path=path, error=_api_error_code(exc))
                )
            except (DockerException, tarfile.TarError) as exc:
                responses.append(FileDownloadResponse(path=path, error=str(exc)))
            except Exception as exc:  # noqa: BLE001 - batch contract requires per-file errors
                responses.append(FileDownloadResponse(path=path, error=str(exc)))
        return responses
