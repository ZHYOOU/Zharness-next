"""Thread-scoped Docker sandbox lifecycle management. / 线程作用域的 Docker 沙箱生命周期管理。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any, Final

from docker.errors import APIError, DockerException, NotFound
from docker.utils import parse_bytes

import docker
from zharness.config import DockerSandboxSettings
from zharness.config.loader import get_settings
from zharness.host.paths import ensure_thread_workspace, thread_workspace_path
from zharness.sandbox.docker import DockerSandbox

SANDBOX_LABEL: Final = "zharness.sandbox"
THREAD_LABEL: Final = "zharness.thread_id"
POLICY_LABEL: Final = "zharness.policy"
POLICY_VERSION: Final = 1

logger = logging.getLogger(__name__)


class SandboxUnavailableError(RuntimeError):
    """Raised when a thread sandbox cannot be provisioned. / 当线程沙箱无法创建时抛出。"""


class SandboxConfigurationMismatchError(SandboxUnavailableError):
    """Signal that an owned container must be rebuilt. / 表示所属容器必须重建。"""


class DockerSandboxManager:
    """Create or reuse one hardened Docker container per server thread. / 为每个服务器线程创建或复用加固后的 Docker 容器。"""

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
        try:
            image_id = str(self.client.images.get(self.settings.image).id)
        except DockerException as exc:
            raise SandboxUnavailableError(
                f"Could not resolve Docker sandbox image: {exc}"
            ) from exc
        policy = self._policy_fingerprint(image_id)
        with self._lock:
            try:
                container = self.client.containers.get(name)
                self._validate_container(
                    container,
                    thread_id,
                    str(workspace),
                    image_id=image_id,
                    policy=policy,
                )
                container.reload()
                if container.status != "running":
                    container.start()
            except NotFound:
                try:
                    container = self._create_container(
                        name,
                        thread_id,
                        str(workspace),
                        image_id=image_id,
                        policy=policy,
                    )
                except DockerException as exc:
                    raise SandboxUnavailableError(
                        f"Could not create Docker sandbox: {exc}"
                    ) from exc
            except SandboxConfigurationMismatchError:
                try:
                    container.remove(force=True)
                    container = self._create_container(
                        name,
                        thread_id,
                        str(workspace),
                        image_id=image_id,
                        policy=policy,
                    )
                except DockerException as exc:
                    raise SandboxUnavailableError(
                        f"Could not rebuild Docker sandbox: {exc}"
                    ) from exc
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not prepare Docker sandbox: {exc}"
                ) from exc
        return DockerSandbox(container)

    def _effective_user(self) -> str | None:
        """Resolve the configured container identity. / 解析配置的容器身份。"""
        if self.settings.user is not None:
            return self.settings.user
        if hasattr(os, "getuid"):
            return f"{os.getuid()}:{os.getgid()}"
        return None

    def _policy_fingerprint(self, image_id: str) -> str:
        """Hash every setting that affects sandbox isolation. / 对影响沙箱隔离的全部设置计算哈希。"""
        policy = {
            "version": POLICY_VERSION,
            "image_id": image_id,
            "memory_limit": self.settings.memory_limit,
            "nano_cpus": self.settings.nano_cpus,
            "pids_limit": self.settings.pids_limit,
            "user": self._effective_user(),
            "skills_root": self.settings.skills_root,
            "network_enabled": self.settings.network_enabled,
        }
        encoded = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _create_container(
        self,
        name: str,
        thread_id: str,
        workspace: str,
        *,
        image_id: str,
        policy: str,
    ) -> Any:
        user = self._effective_user()

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
            "labels": {
                SANDBOX_LABEL: "true",
                THREAD_LABEL: thread_id,
                POLICY_LABEL: policy,
            },
        }
        if user:
            options["user"] = user
        try:
            return self.client.containers.run(**options)
        except APIError as exc:
            if exc.status_code != 409:
                raise
            container = self.client.containers.get(name)
            self._validate_container(
                container,
                thread_id,
                workspace,
                image_id=image_id,
                policy=policy,
            )
            return container

    def remove_for_thread(self, thread_id: str) -> bool:
        """Force-remove this thread's container, returning whether one existed. / 强制移除该线程的容器，并返回容器是否存在。"""

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
            self._validate_ownership(container, thread_id, workspace)
            try:
                container.remove(force=True)
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not remove Docker sandbox: {exc}"
                ) from exc
            return True

    def stop_all(self, *, timeout: int = 10) -> list[str]:
        """Stop every running ZHarness sandbox without deleting it. / 停止所有正在运行的 ZHarness 沙箱，但不删除它们。"""

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

    @staticmethod
    def _validate_ownership(container: Any, thread_id: str, workspace: str) -> None:
        """Validate ownership before mutating an existing container. / 在修改现有容器前验证其归属。"""
        attrs = container.attrs
        config = attrs.get("Config", {})
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

    def _validate_container(
        self,
        container: Any,
        thread_id: str,
        workspace: str,
        *,
        image_id: str,
        policy: str,
    ) -> None:
        self._validate_ownership(container, thread_id, workspace)

        attrs = container.attrs
        config = attrs.get("Config", {})
        host_config = attrs.get("HostConfig", {})
        labels = config.get("Labels", {}) or {}
        mounts = attrs.get("Mounts", [])

        security_options = host_config.get("SecurityOpt", []) or []
        expected_network_mode = "bridge" if self.settings.network_enabled else "none"
        expected_user = self._effective_user() or ""
        expected_memory = parse_bytes(self.settings.memory_limit)
        skills_mounts = [
            mount for mount in mounts if mount.get("Destination") == "/mnt/skills"
        ]
        valid_skills_mount = (
            not skills_mounts
            if self.settings.skills_root is None
            else len(skills_mounts) == 1
            and os.path.realpath(skills_mounts[0].get("Source", ""))
            == os.path.realpath(self.settings.skills_root)
            and skills_mounts[0].get("RW") is False
        )
        hardened = (
            labels.get(POLICY_LABEL) == policy
            and attrs.get("Image") == image_id
            and config.get("User", "") == expected_user
            and host_config.get("ReadonlyRootfs") is True
            and host_config.get("NetworkMode") == expected_network_mode
            and set(host_config.get("CapDrop", []) or []) == {"ALL"}
            and any("no-new-privileges" in option for option in security_options)
            and host_config.get("Memory") == expected_memory
            and host_config.get("NanoCpus") == self.settings.nano_cpus
            and host_config.get("PidsLimit") == self.settings.pids_limit
            and host_config.get("Init") is True
            and host_config.get("Tmpfs", {}).get("/tmp")
            == "rw,nosuid,nodev,noexec,size=64m"
            and valid_skills_mount
        )
        if not hardened:
            raise SandboxConfigurationMismatchError(
                "Existing sandbox does not match the required security policy"
            )


_manager: DockerSandboxManager | object | None = None
_manager_lock = threading.Lock()


def get_sandbox_manager() -> DockerSandboxManager | object:
    """Return the sandbox manager for the configured provider.

    ``sandbox.provider`` (``ZHARNESS_SANDBOX_PROVIDER``) set to ``local``
    selects the local filesystem sandbox; anything else selects the Docker
    sandbox.

    ``sandbox.provider``（``ZHARNESS_SANDBOX_PROVIDER``）为 ``local`` 时选择本地文件系统沙箱；其他值选择 Docker 沙箱。
    """
    global _manager
    with _manager_lock:
        if _manager is None:
            provider = get_settings().sandbox.provider.lower()
            if provider == "local":
                from zharness.sandbox.local import (
                    LocalSandboxManager,
                    LocalSandboxSettings,
                )

                _manager = LocalSandboxManager(settings=LocalSandboxSettings.from_env())
            else:
                _manager = DockerSandboxManager()
        return _manager
