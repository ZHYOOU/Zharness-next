import os
import re
from pathlib import Path
from typing import Final

ZHARNESS_HOME_ENV = "ZHARNESS_HOME"
THREAD_ID_PATTERN: Final = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_THREAD_ID_PATTERN = re.compile(THREAD_ID_PATTERN)


class WorkspacePathError(ValueError):
    """Raised when a server-owned workspace path cannot be resolved safely."""


def zharness_home() -> Path:
    """Return the server-owned ZHarness data directory."""

    configured_home = os.environ.get(ZHARNESS_HOME_ENV)
    home = Path(configured_home) if configured_home else Path.cwd() / ".zharness"

    try:
        return home.expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkspacePathError("Could not resolve ZHarness home") from exc


def thread_workspace_path(
    thread_id: str,
    *,
    home: str | Path | None = None,
) -> Path:
    """Derive a thread workspace beneath the server-owned data directory."""

    if not isinstance(thread_id, str) or not _THREAD_ID_PATTERN.fullmatch(thread_id):
        raise WorkspacePathError("Invalid thread ID")

    try:
        base = (
            Path(home).expanduser().resolve(strict=False) if home else zharness_home()
        )
        workspaces = (base / "workspaces").resolve(strict=False)
        workspace = (workspaces / thread_id).resolve(strict=False)
        workspace.relative_to(workspaces)
    except ValueError as exc:
        raise WorkspacePathError("Thread workspace escapes ZHarness home") from exc
    except (OSError, RuntimeError) as exc:
        raise WorkspacePathError("Could not resolve thread workspace") from exc

    return workspace


def ensure_thread_workspace(
    thread_id: str,
    *,
    home: str | Path | None = None,
) -> Path:
    """Create and return the isolated workspace for a server thread."""

    workspace = thread_workspace_path(thread_id, home=home)

    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspacePathError("Could not create thread workspace") from exc

    if not workspace.is_dir():
        raise WorkspacePathError("Thread workspace is not a directory")

    return workspace
