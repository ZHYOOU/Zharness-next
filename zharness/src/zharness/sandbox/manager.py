"""Thread-scoped Docker sandbox lifecycle management. / 线程作用域的 Docker 沙箱生命周期管理。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime
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
        if self.settings.idle_ttl_seconds < 0:
            raise ValueError("sandbox idle TTL must be non-negative")
        if self.settings.max_containers < 0:
            raise ValueError("sandbox maximum container count must be non-negative")
        if self.settings.cleanup_interval_seconds < 1:
            raise ValueError("sandbox cleanup interval must be positive")
        self._lock = threading.Lock()
        self._last_used: dict[str, float] = {}
        self._active_operations: dict[str, int] = {}

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
        created = False
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
                    created = True
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
            container_id = str(container.id)
            self._last_used[container_id] = time.time()
        if created and self.settings.max_containers > 0:
            self._operation_started(container_id)
            try:
                self.prune()
            except SandboxUnavailableError:
                logger.exception(
                    "Failed to enforce sandbox container limit after creation"
                )
            finally:
                self._operation_finished(container_id)
        return DockerSandbox(
            container,
            on_operation_start=lambda: self._operation_started(container_id),
            on_operation_end=lambda: self._operation_finished(container_id),
        )

    def _operation_started(self, container_id: str) -> None:
        """Protect a container while one of its sandbox operations is running. / 在沙箱操作运行期间保护对应容器。"""

        with self._lock:
            self._active_operations[container_id] = (
                self._active_operations.get(container_id, 0) + 1
            )
            self._last_used[container_id] = time.time()

    def _operation_finished(self, container_id: str) -> None:
        """Release an operation lease and record the latest activity time. / 释放操作租约并记录最新活跃时间。"""

        with self._lock:
            remaining = self._active_operations.get(container_id, 0) - 1
            if remaining > 0:
                self._active_operations[container_id] = remaining
            else:
                self._active_operations.pop(container_id, None)
            self._last_used[container_id] = time.time()

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
        """Remove a thread's container and workspace, returning whether either existed. / 删除线程的容器和工作区，并返回其中是否有资源存在。"""

        workspace = thread_workspace_path(thread_id)
        name = self.container_name(thread_id)
        with self._lock:
            removed = False
            try:
                container = self.client.containers.get(name)
            except NotFound:
                container = None
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not inspect Docker sandbox: {exc}"
                ) from exc
            if container is not None:
                self._validate_ownership(container, thread_id, str(workspace))
                try:
                    container.remove(force=True)
                except DockerException as exc:
                    raise SandboxUnavailableError(
                        f"Could not remove Docker sandbox: {exc}"
                    ) from exc
                container_id = str(container.id)
                self._last_used.pop(container_id, None)
                self._active_operations.pop(container_id, None)
                removed = True

            if workspace.exists():
                try:
                    shutil.rmtree(workspace)
                except OSError as exc:
                    raise SandboxUnavailableError(
                        f"Could not remove sandbox workspace: {exc}"
                    ) from exc
                removed = True
            return removed

    @staticmethod
    def _container_created_at(container: Any, *, fallback: float) -> float:
        """Return a container creation timestamp for restart-safe cleanup ordering. / 返回容器创建时间戳，用于重启后仍可安全排序清理。"""

        value = container.attrs.get("Created")
        if not isinstance(value, str):
            return fallback
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return fallback

    def prune(self, *, now: float | None = None) -> list[str]:
        """Remove idle or over-limit containers while preserving their workspaces. / 删除空闲或超出数量限制的容器，同时保留其工作区。"""

        current_time = time.time() if now is None else now
        with self._lock:
            try:
                containers = self.client.containers.list(
                    all=True,
                    filters={"label": f"{SANDBOX_LABEL}=true"},
                )
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not list Docker sandboxes: {exc}"
                ) from exc

            records: list[tuple[float, str, Any]] = []
            for container in containers:
                container_id = str(container.id)
                last_used = self._last_used.get(container_id)
                if last_used is None:
                    last_used = self._container_created_at(
                        container,
                        fallback=current_time,
                    )
                records.append((last_used, container_id, container))

            remove_ids: set[str] = set()
            if self.settings.idle_ttl_seconds > 0:
                remove_ids.update(
                    container_id
                    for last_used, container_id, _container in records
                    if self._active_operations.get(container_id, 0) == 0
                    and current_time - last_used >= self.settings.idle_ttl_seconds
                )

            retained = [record for record in records if record[1] not in remove_ids]
            if (
                self.settings.max_containers > 0
                and len(retained) > self.settings.max_containers
            ):
                excess = len(retained) - self.settings.max_containers
                removable = sorted(
                    (
                        record
                        for record in retained
                        if self._active_operations.get(record[1], 0) == 0
                    ),
                    key=lambda record: (record[0], record[1]),
                )
                remove_ids.update(record[1] for record in removable[:excess])

            removed: list[str] = []
            for _last_used, container_id, container in records:
                if container_id not in remove_ids:
                    continue
                try:
                    container.remove(force=True)
                    removed.append(container_id)
                except NotFound:
                    continue
                except DockerException:
                    logger.exception(
                        "Failed to prune sandbox container %s",
                        container_id,
                    )
                finally:
                    self._last_used.pop(container_id, None)
                    self._active_operations.pop(container_id, None)
            return removed

    def shutdown_all(self) -> list[str]:
        """Force-remove every ZHarness container while preserving workspaces. / 强制删除所有 ZHarness 容器，同时保留工作区。"""

        with self._lock:
            try:
                containers = self.client.containers.list(
                    all=True,
                    filters={"label": f"{SANDBOX_LABEL}=true"},
                )
            except DockerException as exc:
                raise SandboxUnavailableError(
                    f"Could not list Docker sandboxes: {exc}"
                ) from exc

            removed: list[str] = []
            for container in containers:
                container_id = str(container.id)
                try:
                    container.remove(force=True)
                    removed.append(container_id)
                except NotFound:
                    continue
                except DockerException:
                    logger.exception(
                        "Failed to remove sandbox container %s during shutdown",
                        container_id,
                    )
            self._last_used.clear()
            self._active_operations.clear()
            return removed

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
