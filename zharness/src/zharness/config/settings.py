"""Typed configuration settings for ZHarness. / ZHarness 的类型化配置项。

Settings are read from ``zharness/config.yaml`` and overridden by the matching
``ZHARNESS_*`` environment variables. Each dataclass mirrors one section of the
YAML file; ``from_env`` builds sandbox settings with the derived skills mount.

配置从 ``zharness/config.yaml`` 读取，并由对应的 ``ZHARNESS_*`` 环境变量覆盖。
每个数据类对应 YAML 文件的一个段落；``from_env`` 会构建带派生技能挂载的沙箱配置。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

DEFAULT_MODEL_NAME = "deepseek-chat"
"""Default chat model name. / 默认聊天模型名称。"""

DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 2024

DEFAULT_SANDBOX_PROVIDER = "docker"
DEFAULT_SANDBOX_IMAGE = "zharness-sandbox:latest"
DEFAULT_SANDBOX_MEMORY_LIMIT = "512m"
DEFAULT_SANDBOX_NANO_CPUS = 1_000_000_000
DEFAULT_SANDBOX_PIDS_LIMIT = 128

DEFAULT_POSTGRES_MANAGED = True
DEFAULT_POSTGRES_USER = "zharness"
DEFAULT_POSTGRES_PASSWORD = "change-me"
DEFAULT_POSTGRES_DB = "zharness"
DEFAULT_POSTGRES_PORT = 5432


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """Chat model provider settings. / 聊天模型提供商配置。"""

    name: str = DEFAULT_MODEL_NAME
    provider: str | None = None
    openai_base_url: str | None = None
    anthropic_base_url: str | None = None


@dataclass(frozen=True, slots=True)
class ServerSettings:
    """LangGraph server bind settings. / LangGraph 服务绑定配置。"""

    host: str = DEFAULT_SERVER_HOST
    port: int = DEFAULT_SERVER_PORT


@dataclass(frozen=True, slots=True)
class DockerSandboxSettings:
    """Docker sandbox provider settings. / Docker 沙箱提供程序配置。"""

    image: str = DEFAULT_SANDBOX_IMAGE
    memory_limit: str = DEFAULT_SANDBOX_MEMORY_LIMIT
    nano_cpus: int = DEFAULT_SANDBOX_NANO_CPUS
    pids_limit: int = DEFAULT_SANDBOX_PIDS_LIMIT
    user: str | None = None
    skills_root: str | None = None
    network_enabled: bool = True

    @classmethod
    def from_env(cls) -> DockerSandboxSettings:
        """Build settings from YAML with environment overrides. / 从 YAML 构建配置，并应用环境变量覆盖。"""
        from zharness.config.loader import get_settings

        cfg = get_settings().sandbox.docker
        return cls(
            image=cfg.image,
            memory_limit=cfg.memory_limit,
            nano_cpus=cfg.nano_cpus,
            pids_limit=cfg.pids_limit,
            user=cfg.user,
            network_enabled=cfg.network_enabled,
            skills_root=_resolved_skills_root(),
        )


@dataclass(frozen=True, slots=True)
class LocalSandboxSettings:
    """Local sandbox provider settings. / 本地沙箱提供程序配置。"""

    root: str | None = None
    allow_host_bash: bool = False
    skills_root: str | None = None

    @classmethod
    def from_env(cls) -> LocalSandboxSettings:
        """Build settings from YAML with environment overrides. / 从 YAML 构建配置，并应用环境变量覆盖。"""
        from zharness.config.loader import get_settings

        cfg = get_settings().sandbox.local
        return cls(
            root=cfg.root,
            allow_host_bash=cfg.allow_host_bash,
            skills_root=_resolved_skills_root(),
        )


def _resolved_skills_root() -> str | None:
    """Resolve the configured skills directory for a sandbox mount, if any. / 解析沙箱挂载已配置的技能目录（如有）。"""
    from zharness.skills.storage import skills_root_path

    try:
        root = skills_root_path()
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to resolve skills root for sandbox mount"
        )
        return None
    return str(root) if root.is_dir() else None


@dataclass(frozen=True, slots=True)
class SandboxSettings:
    """Sandbox backend selection plus provider-specific settings. / 沙箱后端选择及各提供程序配置。"""

    provider: str = DEFAULT_SANDBOX_PROVIDER
    docker: DockerSandboxSettings = field(default_factory=DockerSandboxSettings)
    local: LocalSandboxSettings = field(default_factory=LocalSandboxSettings)


@dataclass(frozen=True, slots=True)
class PostgresSettings:
    """PostgreSQL checkpoint persistence settings. / PostgreSQL 检查点持久化配置。"""

    managed: bool = DEFAULT_POSTGRES_MANAGED
    uri: str | None = None
    user: str = DEFAULT_POSTGRES_USER
    password: str = DEFAULT_POSTGRES_PASSWORD
    database: str = DEFAULT_POSTGRES_DB
    port: int = DEFAULT_POSTGRES_PORT


@dataclass(frozen=True, slots=True)
class SkillsSettings:
    """Skills directory override. / 技能目录覆盖配置。"""

    path: str | None = None


@dataclass(frozen=True, slots=True)
class LangsmithSettings:
    """LangSmith observability settings. / LangSmith 可观测性配置。"""

    tracing: bool = False
    project: str | None = None


@dataclass(frozen=True, slots=True)
class Settings:
    """Root settings object mirroring the YAML config file. / 对应 YAML 配置文件结构的根配置对象。"""

    model: ModelSettings = field(default_factory=ModelSettings)
    server: ServerSettings = field(default_factory=ServerSettings)
    home: str | None = None
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)
    postgres: PostgresSettings = field(default_factory=PostgresSettings)
    skills: SkillsSettings = field(default_factory=SkillsSettings)
    langsmith: LangsmithSettings = field(default_factory=LangsmithSettings)
