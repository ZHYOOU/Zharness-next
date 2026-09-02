"""Load ZHarness settings from YAML with environment-variable overrides.

Precedence per setting: a set ``ZHARNESS_*`` (or ``LANGSMITH_*``) environment
variable always wins, then the YAML file, then the built-in default. Secrets
(API keys, the managed PostgreSQL password, an explicit PostgreSQL URI) are
kept in ``zharness/.env`` and loaded by the host framework; only non-secret
settings belong in the YAML file.

从 YAML 加载 ZHarness 配置，并支持环境变量覆盖。

每个配置项的优先级：已设置的 ``ZHARNESS_*``（或 ``LANGSMITH_*``）环境变量始终优先，
其次是 YAML 文件，最后是内置默认值。密钥（API Key、托管 PostgreSQL 密码、显式
PostgreSQL URI）保留在 ``zharness/.env`` 中，由宿主框架加载；YAML 文件只放非敏感配置。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

import yaml

from zharness.config.settings import (
    DEFAULT_MODEL_NAME,
    DEFAULT_POSTGRES_DB,
    DEFAULT_POSTGRES_MANAGED,
    DEFAULT_POSTGRES_PASSWORD,
    DEFAULT_POSTGRES_PORT,
    DEFAULT_POSTGRES_USER,
    DEFAULT_SANDBOX_CLEANUP_INTERVAL_SECONDS,
    DEFAULT_SANDBOX_IDLE_TTL_SECONDS,
    DEFAULT_SANDBOX_IMAGE,
    DEFAULT_SANDBOX_MAX_CONTAINERS,
    DEFAULT_SANDBOX_MEMORY_LIMIT,
    DEFAULT_SANDBOX_NANO_CPUS,
    DEFAULT_SANDBOX_PIDS_LIMIT,
    DEFAULT_SANDBOX_PROVIDER,
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
    DockerSandboxSettings,
    LangsmithSettings,
    LocalSandboxSettings,
    ModelSettings,
    PostgresSettings,
    SandboxSettings,
    ServerSettings,
    Settings,
    SkillsSettings,
)

CONFIG_PATH_ENV: Final = "ZHARNESS_CONFIG"
"""Environment variable that overrides the YAML config file location. / 覆盖 YAML 配置文件位置的环境变量。"""

DEFAULT_CONFIG_FILE: Final = "config.yaml"
"""YAML config file name next to the repository package directory. / 仓库包目录旁的 YAML 配置文件名称。"""

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def resolve_config_path() -> Path:
    """Return the YAML config file to load, falling back to the repository file. / 返回要加载的 YAML 配置文件，回退到仓库内文件。"""

    configured = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return Path(__file__).resolve().parents[3] / DEFAULT_CONFIG_FILE


def _load_yaml(path: Path) -> dict[str, Any]:
    """Return the YAML mapping, or an empty mapping when the file is absent. / 返回 YAML 映射；文件不存在时返回空映射。"""

    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError:
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TypeError(f"Config file {path} must contain a mapping at the top level")
    return data


def _env(name: str) -> str | None:
    """Return the trimmed environment value, or ``None`` when unset or blank. / 返回去除空白后的环境变量值；未设置或为空时返回 ``None``。"""

    value = os.environ.get(name, "").strip()
    return value or None


def _env_bool(name: str) -> bool | None:
    value = _env(name)
    if value is None:
        return None
    return value.lower() not in _FALSE_VALUES


def _env_int(name: str) -> int | None:
    value = _env(name)
    return int(value) if value is not None else None


def _pick(name: str, yaml_value: Any, default: str | None) -> str | None:
    """Resolve a string setting: environment, then YAML, then default. / 解析字符串配置：环境变量优先，其次 YAML，最后默认值。"""

    env_value = _env(name)
    if env_value is not None:
        return env_value
    if yaml_value is not None:
        return str(yaml_value)
    return default


def _pick_bool(name: str, yaml_value: Any, default: bool) -> bool:
    """Resolve a boolean setting: environment, then YAML, then default. / 解析布尔配置：环境变量优先，其次 YAML，最后默认值。"""

    env_value = _env_bool(name)
    if env_value is not None:
        return env_value
    if yaml_value is not None:
        return bool(yaml_value)
    return default


def _pick_int(name: str, yaml_value: Any, default: int) -> int:
    """Resolve an integer setting: environment, then YAML, then default. / 解析整数配置：环境变量优先，其次 YAML，最后默认值。"""

    env_value = _env_int(name)
    if env_value is not None:
        return env_value
    if yaml_value is not None:
        return int(yaml_value)
    return default


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings, applying environment-variable overrides on top of YAML. / 加载配置，在 YAML 之上应用环境变量覆盖。"""

    config_path = Path(path) if path is not None else resolve_config_path()
    data = _load_yaml(config_path)

    from zharness.skills.constants import ZHARNESS_SKILLS_PATH_ENV

    model = data.get("model") or {}
    server = data.get("server") or {}
    sandbox = data.get("sandbox") or {}
    docker = sandbox.get("docker") or {}
    local = sandbox.get("local") or {}
    postgres = data.get("postgres") or {}
    skills = data.get("skills") or {}
    langsmith = data.get("langsmith") or {}

    return Settings(
        model=ModelSettings(
            name=_pick("ZHARNESS_MODEL", model.get("name"), DEFAULT_MODEL_NAME),
            provider=_pick("ZHARNESS_MODEL_PROVIDER", model.get("provider"), None),
            openai_base_url=_pick(
                "ZHARNESS_OPENAI_BASE_URL", model.get("openai_base_url"), None
            ),
            anthropic_base_url=_pick(
                "ZHARNESS_ANTHROPIC_BASE_URL",
                model.get("anthropic_base_url"),
                None,
            ),
        ),
        server=ServerSettings(
            host=_pick("ZHARNESS_SERVER_HOST", server.get("host"), DEFAULT_SERVER_HOST),
            port=_pick_int(
                "ZHARNESS_SERVER_PORT", server.get("port"), DEFAULT_SERVER_PORT
            ),
        ),
        home=_pick("ZHARNESS_HOME", data.get("home"), None),
        sandbox=SandboxSettings(
            provider=_pick(
                "ZHARNESS_SANDBOX_PROVIDER",
                sandbox.get("provider"),
                DEFAULT_SANDBOX_PROVIDER,
            ),
            docker=DockerSandboxSettings(
                image=_pick(
                    "ZHARNESS_SANDBOX_IMAGE",
                    docker.get("image"),
                    DEFAULT_SANDBOX_IMAGE,
                ),
                memory_limit=_pick(
                    "ZHARNESS_SANDBOX_MEMORY",
                    docker.get("memory_limit"),
                    DEFAULT_SANDBOX_MEMORY_LIMIT,
                ),
                nano_cpus=_pick_int(
                    "ZHARNESS_SANDBOX_NANO_CPUS",
                    docker.get("nano_cpus"),
                    DEFAULT_SANDBOX_NANO_CPUS,
                ),
                pids_limit=_pick_int(
                    "ZHARNESS_SANDBOX_PIDS_LIMIT",
                    docker.get("pids_limit"),
                    DEFAULT_SANDBOX_PIDS_LIMIT,
                ),
                user=_pick("ZHARNESS_SANDBOX_USER", docker.get("user"), None),
                network_enabled=_pick_bool(
                    "ZHARNESS_SANDBOX_NETWORK",
                    docker.get("network_enabled"),
                    True,
                ),
                idle_ttl_seconds=_pick_int(
                    "ZHARNESS_SANDBOX_IDLE_TTL_SECONDS",
                    docker.get("idle_ttl_seconds"),
                    DEFAULT_SANDBOX_IDLE_TTL_SECONDS,
                ),
                max_containers=_pick_int(
                    "ZHARNESS_SANDBOX_MAX_CONTAINERS",
                    docker.get("max_containers"),
                    DEFAULT_SANDBOX_MAX_CONTAINERS,
                ),
                cleanup_interval_seconds=_pick_int(
                    "ZHARNESS_SANDBOX_CLEANUP_INTERVAL_SECONDS",
                    docker.get("cleanup_interval_seconds"),
                    DEFAULT_SANDBOX_CLEANUP_INTERVAL_SECONDS,
                ),
            ),
            local=LocalSandboxSettings(
                root=_pick("ZHARNESS_LOCAL_ROOT", local.get("root"), None),
                allow_host_bash=_pick_bool(
                    "ZHARNESS_ALLOW_HOST_BASH",
                    local.get("allow_host_bash"),
                    False,
                ),
            ),
        ),
        postgres=PostgresSettings(
            managed=_pick_bool(
                "ZHARNESS_POSTGRES_MANAGED",
                postgres.get("managed"),
                DEFAULT_POSTGRES_MANAGED,
            ),
            uri=_pick("ZHARNESS_POSTGRES_URI", postgres.get("uri"), None),
            user=_pick(
                "ZHARNESS_POSTGRES_USER",
                postgres.get("user"),
                DEFAULT_POSTGRES_USER,
            ),
            password=_pick(
                "ZHARNESS_POSTGRES_PASSWORD",
                postgres.get("password"),
                DEFAULT_POSTGRES_PASSWORD,
            ),
            database=_pick(
                "ZHARNESS_POSTGRES_DB",
                postgres.get("database"),
                DEFAULT_POSTGRES_DB,
            ),
            port=_pick_int(
                "ZHARNESS_POSTGRES_PORT", postgres.get("port"), DEFAULT_POSTGRES_PORT
            ),
        ),
        skills=SkillsSettings(
            path=_pick(ZHARNESS_SKILLS_PATH_ENV, skills.get("path"), None),
        ),
        langsmith=LangsmithSettings(
            tracing=_pick_bool("LANGSMITH_TRACING", langsmith.get("tracing"), False),
            project=_pick("LANGSMITH_PROJECT", langsmith.get("project"), None),
        ),
    )


def get_settings() -> Settings:
    """Return the current settings, reading the environment fresh on every call. / 返回当前配置，每次调用都会重新读取环境变量。"""

    return load_settings()
