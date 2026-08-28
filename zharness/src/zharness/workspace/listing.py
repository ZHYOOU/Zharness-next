from pathlib import Path

MAX_WORKSPACE_ENTRIES = 100


def list_workspace_entries(workspace_path: str) -> list[str]:
    root = Path(workspace_path).resolve(strict=True)

    if not root.is_dir():
        raise NotADirectoryError(workspace_path)

    entries = sorted(
        (
            f"{entry.name}/" if entry.is_dir() else entry.name
            for entry in root.iterdir()
        ),
        key=str.casefold,
    )

    return entries[:MAX_WORKSPACE_ENTRIES]
