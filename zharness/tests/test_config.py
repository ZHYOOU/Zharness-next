"""Tests for the YAML-backed configuration module. / YAML 中央配置模块的测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from zharness.config import get_settings, load_settings, resolve_config_path
from zharness.config.loader import CONFIG_PATH_ENV

_ZHARNESS_ENV_VARS = (
    "ZHARNESS_MODEL",
    "ZHARNESS_MODEL_PROVIDER",
    "ZHARNESS_OPENAI_BASE_URL",
    "ZHARNESS_ANTHROPIC_BASE_URL",
    "ZHARNESS_MIMO_BASE_URL",
    "ZHARNESS_SERVER_HOST",
    "ZHARNESS_SERVER_PORT",
    "ZHARNESS_HOME",
    "ZHARNESS_TIMEZONE",
    "ZHARNESS_SANDBOX_PROVIDER",
    "ZHARNESS_SANDBOX_IMAGE",
    "ZHARNESS_SANDBOX_MEMORY",
    "ZHARNESS_SANDBOX_NANO_CPUS",
    "ZHARNESS_SANDBOX_PIDS_LIMIT",
    "ZHARNESS_SANDBOX_USER",
    "ZHARNESS_SANDBOX_NETWORK",
    "ZHARNESS_SANDBOX_IDLE_TTL_SECONDS",
    "ZHARNESS_SANDBOX_MAX_CONTAINERS",
    "ZHARNESS_SANDBOX_CLEANUP_INTERVAL_SECONDS",
    "ZHARNESS_LOCAL_ROOT",
    "ZHARNESS_ALLOW_HOST_BASH",
    "ZHARNESS_POSTGRES_MANAGED",
    "ZHARNESS_POSTGRES_URI",
    "ZHARNESS_POSTGRES_USER",
    "ZHARNESS_POSTGRES_PASSWORD",
    "ZHARNESS_POSTGRES_DB",
    "ZHARNESS_POSTGRES_PORT",
    "ZHARNESS_SKILLS_PATH",
    "LANGSMITH_TRACING",
    "LANGSMITH_PROJECT",
)


@pytest.fixture(autouse=True)
def _clear_zharness_env(monkeypatch) -> None:
    """Isolate every test from the host environment. / 隔离每个测试与宿主环境的联系。"""
    for name in _ZHARNESS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_defaults_without_config_file(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing.yaml")

    assert settings.model.name == "mimo-v2.5"
    assert settings.model.provider is None
    assert settings.model.openai_base_url is None
    assert settings.model.mimo_base_url is None
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 2024
    assert settings.home is None
    assert settings.timezone == "Asia/Shanghai"
    assert settings.sandbox.provider == "docker"
    assert settings.sandbox.docker.image == "zharness-sandbox:latest"
    assert settings.sandbox.docker.memory_limit == "512m"
    assert settings.sandbox.docker.nano_cpus == 1_000_000_000
    assert settings.sandbox.docker.pids_limit == 128
    assert settings.sandbox.docker.user is None
    assert settings.sandbox.docker.network_enabled is True
    assert settings.sandbox.docker.idle_ttl_seconds == 86_400
    assert settings.sandbox.docker.max_containers == 5
    assert settings.sandbox.docker.cleanup_interval_seconds == 300
    assert settings.sandbox.local.root is None
    assert settings.sandbox.local.allow_host_bash is False
    assert settings.postgres.managed is True
    assert settings.postgres.uri is None
    assert settings.postgres.user == "zharness"
    assert settings.postgres.password == "change-me"
    assert settings.postgres.database == "zharness"
    assert settings.postgres.port == 5432
    assert settings.skills.path is None
    assert settings.langsmith.tracing is False
    assert settings.langsmith.project is None


def test_yaml_provides_values(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
model:
  name: gpt-5
  provider: openai
  openai_base_url: https://ollama.example/v1
  mimo_base_url: https://mimo.example/v1
server:
  host: 0.0.0.0
  port: 9090
home: /srv/zharness
timezone: America/New_York
sandbox:
  provider: local
  docker:
    idle_ttl_seconds: 7200
    max_containers: 12
    cleanup_interval_seconds: 60
  local:
    root: /tmp/project
    allow_host_bash: true
postgres:
  managed: false
  user: alice
  database: agents
  port: 55432
langsmith:
  tracing: true
  project: my-project
""",
    )

    settings = load_settings(path)

    assert settings.model.name == "gpt-5"
    assert settings.model.provider == "openai"
    assert settings.model.openai_base_url == "https://ollama.example/v1"
    assert settings.model.mimo_base_url == "https://mimo.example/v1"
    assert settings.server.host == "0.0.0.0"
    assert settings.server.port == 9090
    assert settings.home == "/srv/zharness"
    assert settings.timezone == "America/New_York"
    assert settings.sandbox.provider == "local"
    assert settings.sandbox.docker.idle_ttl_seconds == 7200
    assert settings.sandbox.docker.max_containers == 12
    assert settings.sandbox.docker.cleanup_interval_seconds == 60
    assert settings.sandbox.local.root == "/tmp/project"
    assert settings.sandbox.local.allow_host_bash is True
    assert settings.postgres.managed is False
    assert settings.postgres.user == "alice"
    assert settings.postgres.database == "agents"
    assert settings.postgres.port == 55432
    assert settings.langsmith.tracing is True
    assert settings.langsmith.project == "my-project"


def test_environment_overrides_yaml(monkeypatch, tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
model:
  name: gpt-5
  provider: openai
server:
  port: 2024
sandbox:
  docker:
    user: "1000:1000"
    network_enabled: true
""",
    )
    monkeypatch.setenv("ZHARNESS_MODEL", "deepseek-chat")
    monkeypatch.setenv("ZHARNESS_MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("ZHARNESS_SERVER_PORT", "8080")
    monkeypatch.setenv("ZHARNESS_TIMEZONE", "Europe/Paris")
    monkeypatch.setenv("ZHARNESS_SANDBOX_USER", "2000:2000")
    monkeypatch.setenv("ZHARNESS_SANDBOX_NETWORK", "false")
    monkeypatch.setenv("ZHARNESS_SANDBOX_MAX_CONTAINERS", "7")

    settings = load_settings(path)

    assert settings.model.name == "deepseek-chat"
    assert settings.model.provider == "deepseek"
    assert settings.server.port == 8080
    assert settings.timezone == "Europe/Paris"
    assert settings.sandbox.docker.user == "2000:2000"
    assert settings.sandbox.docker.network_enabled is False
    assert settings.sandbox.docker.max_containers == 7


def test_boolean_environment_parsing(monkeypatch, tmp_path: Path) -> None:
    path = _write_config(tmp_path, "")

    monkeypatch.setenv("ZHARNESS_SANDBOX_NETWORK", "false")
    assert load_settings(path).sandbox.docker.network_enabled is False
    monkeypatch.setenv("ZHARNESS_SANDBOX_NETWORK", "0")
    assert load_settings(path).sandbox.docker.network_enabled is False
    monkeypatch.setenv("ZHARNESS_SANDBOX_NETWORK", "no")
    assert load_settings(path).sandbox.docker.network_enabled is False
    monkeypatch.setenv("ZHARNESS_SANDBOX_NETWORK", "true")
    assert load_settings(path).sandbox.docker.network_enabled is True

    monkeypatch.setenv("ZHARNESS_ALLOW_HOST_BASH", "yes")
    assert load_settings(path).sandbox.local.allow_host_bash is True


def test_integer_environment_parsing(monkeypatch, tmp_path: Path) -> None:
    path = _write_config(tmp_path, "")

    monkeypatch.setenv("ZHARNESS_POSTGRES_PORT", "55432")
    monkeypatch.setenv("ZHARNESS_SANDBOX_NANO_CPUS", "2000000000")
    monkeypatch.setenv("ZHARNESS_SANDBOX_PIDS_LIMIT", "64")
    monkeypatch.setenv("ZHARNESS_SANDBOX_IDLE_TTL_SECONDS", "1800")
    monkeypatch.setenv("ZHARNESS_SANDBOX_CLEANUP_INTERVAL_SECONDS", "30")

    settings = load_settings(path)
    assert settings.postgres.port == 55432
    assert settings.sandbox.docker.nano_cpus == 2_000_000_000
    assert settings.sandbox.docker.pids_limit == 64
    assert settings.sandbox.docker.idle_ttl_seconds == 1800
    assert settings.sandbox.docker.cleanup_interval_seconds == 30


def test_resolve_config_path_prefers_environment(monkeypatch, tmp_path: Path) -> None:
    configured = tmp_path / "custom" / "settings.yaml"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(configured))

    assert resolve_config_path() == configured


def test_invalid_top_level_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(TypeError, match="must contain a mapping"):
        load_settings(path)


def test_get_settings_reads_environment_fresh(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("ZHARNESS_MODEL", "deepseek-chat")
    assert get_settings().model.name == "deepseek-chat"
    monkeypatch.setenv("ZHARNESS_MODEL", "gpt-5")
    assert get_settings().model.name == "gpt-5"
