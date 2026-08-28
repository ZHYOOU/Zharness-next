import os
import tempfile
from pathlib import Path

from zharness.workspace.paths import WorkspacePathError, resolve_workspace_path

MAX_WRITE_BYTES = 256 * 1024


class WorkspaceWriteError(ValueError):
    """Raised when a workspace file cannot be written safely."""


def _resolve_write_target(
    workspace_path: str | Path,
    relative_path: str,
) -> Path:
    try:
        target = resolve_workspace_path(workspace_path, relative_path)
    except WorkspacePathError as exc:
        raise WorkspaceWriteError(str(exc)) from exc

    if target.exists() and not target.is_file():
        raise WorkspaceWriteError(f"Path is not a regular file: {relative_path}")

    return target


def write_workspace_file(
    workspace_path: str | Path,
    relative_path: str,
    content: str,
    *,
    max_bytes: int = MAX_WRITE_BYTES,
) -> str:
    """Atomically write UTF-8 text inside a workspace."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")

    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise WorkspaceWriteError(f"Content exceeds the {max_bytes}-byte limit")

    target = _resolve_write_target(workspace_path, relative_path)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceWriteError(
            f"Could not create parent directory: {relative_path}"
        ) from exc

    # Resolve again after creating parents so a pre-existing symlink cannot redirect
    # the write outside the workspace.
    target = _resolve_write_target(workspace_path, relative_path)
    temporary_path: Path | None = None

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(encoded)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, target)
    except OSError as exc:
        raise WorkspaceWriteError(f"Could not write file: {relative_path}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return f"Wrote {len(encoded)} bytes to {relative_path}"
