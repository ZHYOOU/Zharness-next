"""Thread-scoped Docker sandbox lifecycle management."""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Final

from docker.errors import APIError, DockerException, NotFound

import docker
from zharness.sandbox.docker import DockerSandbox
from zharness.workspace.paths import ensure_thread_workspace, thread_workspace_path

SANDBOX_LABEL: Final = "zharness.sandbox"
THREAD_LABEL: Final = "zharness.thread_id"
DEFAULT_IMAGE: Final = "zharness-sandbox:latest"
DEFAULT_MEMORY_LIMIT: Final = "512m"
DEFAULT_NETWORK_ENABLED: Final = True

logger = logging.getLogger(__name__)


class SandboxUnavailableError(RuntimeError):
    """Raised when a thread sandbox cannot be provisioned."""


@dataclass(frozen=True, slots=True)
class DockerSandboxSettings:
    image: str = DEFAULT_IMAGE
    memory_limit: str = DEFAULT_MEMORY_LIMIT
    nano_cpus: int = 1_000_000_000
    pids_limit: int = 128
    user: str | None = None
    skills_root: str | None = None
    network_enabled: bool = DEFAULT_NETWORK_ENABLED

    @classmethod
    def from_env(cls) -> DockerSandboxSettings:
        return cls(
            image=os.environ.get("ZHARNESS_SANDBOX_IMAGE", DEFAULT_IMAGE),
            memory_limit=os.environ.get(
                "ZHARNESS_SANDBOX_MEMORY", DEFAULT_MEMORY_LIMIT
            ),
            network_enabled=os.environ.get("ZHARNESS_SANDBOX_NETWORK", "true")
            .strip()
            .lower()
            not in {"0", "false", "no"},
            user=os.environ.get("ZHARNESS_SANDBOX_USER"),
            skills_root=_env_skills_root(),
        )


def _env_skills_root() -> str | None:
    """Resolve the configured skills directory for a sandbox mount, if any. / 解析沙箱挂载已配置的技能目录（如有）。"""
    from zharness.skills.storage import skills_root_path

    try:
        root = skills_root_path()
    except Exception:
        logger.exception("Failed to resolve skills root for sandbox mount")
        return None
    return str(root) if root.is_dir() else None


class DockerSandboxManager:
    """Create or reuse one hardened Docker container per server thread."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        settings: DockerSandboxSettings | None = None,
    ) -> None:
        self._client = client
        self.settings = settings or DockerSandboxSettings.from_env()
        self._lock = threading.Lock()

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                self._client = docker.from_env()
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not connect to Docker: {exc}"
                ) from exc
        return self._client

    @staticmethod
    def container_name(thread_id: str) -> str:
        digest = hashlib.sha256(thread_id.encode()).hexdigest()[:24]
        return f"zharness-{digest}"

    def for_thread(self, thread_id: str) -> DockerSandbox:
        workspace = ensure_thread_workspace(thread_id)
        name = self.container_name(thread_id)
        with self._lock:
            try:
                container = self.client.containers.get(name)
                self._validate_container(container, thread_id, str(workspace))
                container.reload()
                if container.status != "running":
                    container.start()
            except NotFound:
                try:
                    container = self._create_container(name, thread_id, str(workspace))
                except DockerException as exc:
                    raise SandboxUnavailableError(
                        f"Could not create Docker sandbox: {exc}"
                    ) from exc
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not prepare Docker sandbox: {exc}"
                ) from exc
        return DockerSandbox(container)

    def _create_container(self, name: str, thread_id: str, workspace: str) -> Any:
        user = self.settings.user
        if user is None and hasattr(os, "getuid"):
            user = f"{os.getuid()}:{os.getgid()}"

        volumes: dict[str, dict[str, str]] = {
            workspace: {"bind": "/workspace", "mode": "rw"}
        }
        if self.settings.skills_root:
            volumes[self.settings.skills_root] = {
                "bind": "/mnt/skills",
                "mode": "ro",
            }

        options: dict[str, Any] = {
            "image": self.settings.image,
            "name": name,
            "command": ["sleep", "infinity"],
            "detach": True,
            "working_dir": "/workspace",
            "environment": {"HOME": "/tmp"},
            "volumes": volumes,
            "network_mode": "bridge" if self.settings.network_enabled else "none",
            "read_only": True,
            "tmpfs": {"/tmp": "rw,nosuid,nodev,noexec,size=64m"},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "mem_limit": self.settings.memory_limit,
            "nano_cpus": self.settings.nano_cpus,
            "pids_limit": self.settings.pids_limit,
            "init": True,
            "labels": {SANDBOX_LABEL: "true", THREAD_LABEL: thread_id},
        }
        if user:
            options["user"] = user
        try:
            return self.client.containers.run(**options)
        except APIError as exc:
            if exc.status_code != 409:
                raise
            container = self.client.containers.get(name)
            self._validate_container(container, thread_id, workspace)
            return container

    def remove_for_thread(self, thread_id: str) -> bool:
        """Force-remove this thread's container, returning whether one existed."""

        workspace = str(thread_workspace_path(thread_id))
        name = self.container_name(thread_id)
        with self._lock:
            try:
                container = self.client.containers.get(name)
            except NotFound:
                return False
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not inspect Docker sandbox: {exc}"
                ) from exc
            self._validate_container(container, thread_id, workspace)
            try:
                container.remove(force=True)
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not remove Docker sandbox: {exc}"
                ) from exc
            return True

    def stop_all(self, *, timeout: int = 10) -> list[str]:
        """Stop every running ZHarness sandbox without deleting it."""

        if timeout < 0:
            raise ValueError("stop timeout must be non-negative")

        with self._lock:
            try:
                containers = self.client.containers.list(
                    filters={
                        "label": f"{SANDBOX_LABEL}=true",
                        "status": "running",
                    }
                )
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not list Docker sandboxes: {exc}"
                ) from exc

            stopped: list[str] = []
            for container in containers:
                try:
                    container.stop(timeout=timeout)
                    stopped.append(str(container.id))
                except NotFound:
                    continue
                except DockerException:
                    logger.exception(
                        "Failed to stop sandbox container %s", container.id
                    )
            return stopped

    def _validate_container(
        self, container: Any, thread_id: str, workspace: str
    ) -> None:
        attrs = container.attrs
        config = attrs.get("Config", {})
        host_config = attrs.get("HostConfig", {})
        labels = config.get("Labels", {}) or {}
        if labels.get(SANDBOX_LABEL) != "true" or labels.get(THREAD_LABEL) != thread_id:
            raise SandboxUnavailableError(
                "Refusing to reuse a container not owned by this thread"
            )

        mounts = attrs.get("Mounts", [])
        valid_mount = any(
            mount.get("Destination") == "/workspace"
            and os.path.realpath(mount.get("Source", "")) == os.path.realpath(workspace)
            and mount.get("RW") is True
            for mount in mounts
        )
        if not valid_mount:
            raise SandboxUnavailableError(
                "Existing sandbox has an unexpected workspace mount"
            )

        security_options = host_config.get("SecurityOpt", []) or []
        expected_network_mode = "bridge" if self.settings.network_enabled else "none"
        hardened = (
            host_config.get("ReadonlyRootfs") is True
            and host_config.get("NetworkMode") == expected_network_mode
            and set(host_config.get("CapDrop", []) or []) == {"ALL"}
            and any("no-new-privileges" in option for option in security_options)
        )
        if not hardened:
            raise SandboxUnavailableError(
                "Existing sandbox does not match the required security policy"
            )


_manager: DockerSandboxManager | object | None = None
_manager_lock = threading.Lock()


def get_sandbox_manager() -> DockerSandboxManager | object:
    """Return the sandbox manager for the configured provider.

    ``ZHARNESS_SANDBOX_PROVIDER=local`` selects the local filesystem sandbox;
    anything else selects the Docker sandbox.
    """
    global _manager
    with _manager_lock:
        if _manager is None:
            provider = os.environ.get("ZHARNESS_SANDBOX_PROVIDER", "docker").lower()
            if provider == "local":
                from zharness.sandbox.local import (
                    LocalSandboxManager,
                    LocalSandboxSettings,
                )

                _manager = LocalSandboxManager(settings=LocalSandboxSettings.from_env())
            else:
                _manager = DockerSandboxManager()
        return _manager
