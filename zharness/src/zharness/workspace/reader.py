from pathlib import Path

MAX_READ_BYTES = 256 * 1024


class WorkspaceReadError(ValueError):
    """Raised when a workspace file cannot be read safely."""


def _resolve_workspace_file(
    workspace_path: str,
    relative_path: str,
) -> Path:
    try:
        root = Path(workspace_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceReadError("Workspace does not exist") from exc

    if not root.is_dir():
        raise WorkspaceReadError("Workspace path is not a directory")

    requested = Path(relative_path)

    if requested.is_absolute():
        raise WorkspaceReadError("Absolute paths are not allowed")

    try:
        target = (root / requested).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceReadError(f"Invalid path: {relative_path}") from exc

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise WorkspaceReadError("Path escapes the workspace") from exc

    if not target.exists():
        raise WorkspaceReadError(f"File not found: {relative_path}")

    if not target.is_file():
        raise WorkspaceReadError(f"Path is not a regular file: {relative_path}")

    return target


def read_workspace_file(
    workspace_path: str,
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
