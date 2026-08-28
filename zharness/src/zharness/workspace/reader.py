from pathlib import Path

from zharness.workspace.paths import WorkspacePathError, resolve_workspace_path

MAX_READ_BYTES = 256 * 1024


class WorkspaceReadError(ValueError):
    """Raised when a workspace file cannot be read safely."""


def _resolve_workspace_file(
    workspace_path: str | Path,
    relative_path: str,
) -> Path:
    try:
        target = resolve_workspace_path(workspace_path, relative_path)
    except WorkspacePathError as exc:
        raise WorkspaceReadError(str(exc)) from exc

    if not target.exists():
        raise WorkspaceReadError(f"File not found: {relative_path}")

    if not target.is_file():
        raise WorkspaceReadError(f"Path is not a regular file: {relative_path}")

    return target


def read_workspace_file(
    workspace_path: str | Path,
    relative_path: str,
    *,
    max_bytes: int = MAX_READ_BYTES,
) -> str:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")

    target = _resolve_workspace_file(
        workspace_path,
        relative_path,
    )

    try:
        if target.stat().st_size > max_bytes:
            raise WorkspaceReadError(f"File exceeds the {max_bytes}-byte limit")

        with target.open("rb") as file:
            content = file.read(max_bytes + 1)
    except OSError as exc:
        raise WorkspaceReadError(f"Could not read file: {relative_path}") from exc

    if len(content) > max_bytes:
        raise WorkspaceReadError(f"File exceeds the {max_bytes}-byte limit")

    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceReadError("File is not valid UTF-8 text") from exc
