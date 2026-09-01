"""Remove ZHarness Next runtime data.

This cleans session history (LangGraph checkpoints), thread workspaces, and the
thread-scoped Docker sandbox containers that the project creates, plus optional
Python and lint caches and the sandbox image. It is safe to run while the server
is stopped; the server recreates everything on demand.

Examples:
    uv run --package zharness python scripts/cleanup.py --dry-run
    uv run --package zharness python scripts/cleanup.py -y
    uv run --package zharness python scripts/cleanup.py --caches --remove-image
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from zharness.host.paths import zharness_home
from zharness.sandbox.manager import DEFAULT_IMAGE, SANDBOX_LABEL

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = REPO_ROOT / "zharness" / ".env"
DEFAULT_LANGGRAPH_DATA = REPO_ROOT / ".langgraph_api"
CACHE_DIR_NAMES = ("__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache")
_SKIP_DIR_NAMES = {".venv", ".git", ".idea", ".langgraph_api", ".zharness"}


def _confirm(prompt: str) -> bool:
    try:
        reply = input(f"{prompt} [y/N] ")
    except EOFError:
        return False
    return reply.strip().lower() in {"y", "yes"}


def remove_sessions(data_dir: Path, *, dry_run: bool) -> None:
    if not data_dir.exists():
        print(f"skip: no session data at {data_dir}")
        return
    print(f"remove session data: {data_dir}")
    if not dry_run:
        shutil.rmtree(data_dir)


def remove_workspaces(home: Path, *, dry_run: bool) -> None:
    workspaces = home / "workspaces"
    if not workspaces.exists():
        print(f"skip: no thread workspaces at {workspaces}")
        return
    count = sum(1 for _ in workspaces.iterdir()) if workspaces.is_dir() else 1
    print(f"remove thread workspaces: {workspaces} ({count} workspace(s))")
    if not dry_run:
        shutil.rmtree(workspaces)


def iter_cache_dirs(root: Path) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(root):
        if Path(dirpath) == root:
            dirnames[:] = [name for name in dirnames if name not in _SKIP_DIR_NAMES]
        for name in dirnames:
            if name in CACHE_DIR_NAMES:
                found.append(Path(dirpath) / name)
    return sorted(found)


def remove_caches(root: Path, *, dry_run: bool) -> None:
    cache_dirs = iter_cache_dirs(root)
    if not cache_dirs:
        print(f"skip: no cache directories under {root}")
        return
    print(f"remove {len(cache_dirs)} cache director(y/ies):")
    for cache_dir in cache_dirs:
        print(f"  - {cache_dir}")
        if not dry_run:
            shutil.rmtree(cache_dir)


def _docker_client() -> object | None:
    try:
        from docker.errors import DockerException

        import docker
    except ImportError:
        print("warning: docker package is unavailable; skipping sandbox cleanup")
        return None
    try:
        return docker.from_env()
    except DockerException as exc:
        print(f"warning: could not connect to Docker: {exc}")
        return None


def remove_sandboxes(client: object, *, dry_run: bool) -> None:
    containers = client.containers.list(
        all=True,
        filters={"label": f"{SANDBOX_LABEL}=true"},
    )
    if not containers:
        print("skip: no sandbox containers to remove")
        return
    print(f"remove {len(containers)} sandbox container(s):")
    for container in containers:
        print(f"  - {container.name} ({container.id[:12]})")
        if not dry_run:
            container.remove(force=True)


def remove_image(client: object, image: str, *, dry_run: bool) -> None:
    try:
        client.images.get(image)
    except Exception:  # noqa: BLE001 - any lookup failure means nothing to remove
        print(f"skip: sandbox image not present: {image}")
        return
    print(f"remove sandbox image: {image}")
    if not dry_run:
        client.images.remove(image, force=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Clean ZHarness Next session history, thread workspaces, and Docker "
            "sandbox containers."
        )
    )
    parser.add_argument(
        "--sessions",
        action="store_true",
        help="remove LangGraph session history and store data",
    )
    parser.add_argument(
        "--workspaces",
        action="store_true",
        help="remove per-thread workspaces under ZHARNESS_HOME",
    )
    parser.add_argument(
        "--sandboxes",
        action="store_true",
        help="force-remove thread-scoped Docker sandbox containers",
    )
    parser.add_argument(
        "--caches",
        action="store_true",
        help="also remove Python and lint cache directories",
    )
    parser.add_argument(
        "--remove-image",
        action="store_true",
        help="also remove the sandbox Docker image",
    )
    parser.add_argument(
        "--home",
        help="override ZHARNESS_HOME instead of reading the environment",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_FILE),
        help=f"environment file to load (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="skip the confirmation prompt",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be removed without removing anything",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    env_file = Path(args.env_file)
    if env_file.is_file():
        load_dotenv(env_file, override=False)

    if args.home:
        os.environ["ZHARNESS_HOME"] = args.home

    want = {
        "sessions": args.sessions,
        "workspaces": args.workspaces,
        "sandboxes": args.sandboxes,
    }
    targets_given = any(
        (args.sessions, args.workspaces, args.sandboxes, args.caches, args.remove_image)
    )
    if not targets_given:
        want = {name: True for name in want}

    home = zharness_home()
    data_dir = REPO_ROOT / ".langgraph_api"

    print(f"ZHarness home: {home}")
    print(f"Session data:  {data_dir}")
    if not args.dry_run and not args.yes and not _confirm("\nRemove the listed data?"):
        print("Aborted.")
        return 1

    if want["sessions"]:
        remove_sessions(data_dir, dry_run=args.dry_run)
    if want["workspaces"]:
        remove_workspaces(home, dry_run=args.dry_run)
    if want["sandboxes"] or args.remove_image:
        client = _docker_client()
        if client is not None:
            if want["sandboxes"]:
                remove_sandboxes(client, dry_run=args.dry_run)
            if args.remove_image:
                image = os.environ.get("ZHARNESS_SANDBOX_IMAGE", DEFAULT_IMAGE)
                remove_image(client, image, dry_run=args.dry_run)
    if args.caches:
        remove_caches(REPO_ROOT, dry_run=args.dry_run)

    if args.dry_run:
        print("\nDry run: nothing was removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
