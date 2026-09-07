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
    DEFAULT_KNOWLEDGE_CHUNK_OVERLAP,
    DEFAULT_KNOWLEDGE_CHUNK_SIZE,
    DEFAULT_KNOWLEDGE_EMBEDDING_BATCH_SIZE,
    DEFAULT_KNOWLEDGE_EMBEDDING_DIMENSIONS,
    DEFAULT_KNOWLEDGE_EMBEDDING_MAX_RETRIES,
    DEFAULT_KNOWLEDGE_EMBEDDING_MODEL,
    DEFAULT_KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS,
    DEFAULT_KNOWLEDGE_ENABLED,
    DEFAULT_KNOWLEDGE_FETCH_K,
    DEFAULT_KNOWLEDGE_FUSION_FUNCTION,
    DEFAULT_KNOWLEDGE_HYBRID_ENABLED,
    DEFAULT_KNOWLEDGE_LAMBDA_MULT,
    DEFAULT_KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT,
    DEFAULT_KNOWLEDGE_MAX_CONTEXT_CHARS,
    DEFAULT_KNOWLEDGE_MAX_FILE_BYTES,
    DEFAULT_KNOWLEDGE_MAX_FILES_PER_CALL,
    DEFAULT_KNOWLEDGE_PRIMARY_TOP_K,
    DEFAULT_KNOWLEDGE_RESULT_LIMIT,
    DEFAULT_KNOWLEDGE_RRF_K,
    DEFAULT_KNOWLEDGE_SEARCH_TYPE,
    DEFAULT_KNOWLEDGE_SECONDARY_TOP_K,
    DEFAULT_MEMORY_ENABLED,
    DEFAULT_MEMORY_EXTRACTION_ENABLED,
    DEFAULT_MEMORY_EXTRACTION_MODEL,
    DEFAULT_MEMORY_GATE_ENABLED,
    DEFAULT_MEMORY_INJECT_TOP_K,
    DEFAULT_MEMORY_INJECTION_ENABLED,
    DEFAULT_MEMORY_INJECTION_MAX_CHARS,
    DEFAULT_MEMORY_MAX_FACTS,
    DEFAULT_MEMORY_MIN_CONFIDENCE,
    DEFAULT_MEMORY_SEARCH_LIMIT,
    DEFAULT_MEMORY_USER_ID,
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
    DEFAULT_TIMEZONE,
    DockerSandboxSettings,
    KnowledgeChunkingSettings,
    KnowledgeEmbeddingSettings,
    KnowledgeHybridSettings,
    KnowledgeLimitsSettings,
    KnowledgeRetrievalSettings,
    KnowledgeSettings,
    LangsmithSettings,
    LocalSandboxSettings,
    MemorySettings,
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


def _pick_float(name: str, yaml_value: Any, default: float) -> float:
    """Resolve a float setting: environment, then YAML, then default. / 解析浮点配置：环境变量优先，其次 YAML，最后默认值。"""

    env_value = _env(name)
    if env_value is not None:
        return float(env_value)
    if yaml_value is not None:
        return float(yaml_value)
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
    memory = data.get("memory") or {}
    knowledge = data.get("knowledge") or {}
    knowledge_embedding = knowledge.get("embedding") or {}
    knowledge_chunking = knowledge.get("chunking") or {}
    knowledge_retrieval = knowledge.get("retrieval") or {}
    knowledge_search_kwargs = knowledge_retrieval.get("search_kwargs") or {}
    knowledge_hybrid = knowledge_retrieval.get("hybrid") or {}
    knowledge_fusion_parameters = (
        knowledge_hybrid.get("fusion_function_parameters") or {}
    )
    knowledge_limits = knowledge.get("limits") or {}
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
            mimo_base_url=_pick(
                "ZHARNESS_MIMO_BASE_URL",
                model.get("mimo_base_url"),
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
        timezone=_pick("ZHARNESS_TIMEZONE", data.get("timezone"), DEFAULT_TIMEZONE),
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
        memory=MemorySettings(
            enabled=_pick_bool(
                "ZHARNESS_MEMORY_ENABLED",
                memory.get("enabled"),
                DEFAULT_MEMORY_ENABLED,
            ),
            user_id=_pick(
                "ZHARNESS_MEMORY_USER_ID",
                memory.get("user_id"),
                DEFAULT_MEMORY_USER_ID,
            ),
            max_facts=_pick_int(
                "ZHARNESS_MEMORY_MAX_FACTS",
                memory.get("max_facts"),
                DEFAULT_MEMORY_MAX_FACTS,
            ),
            min_confidence=_pick_float(
                "ZHARNESS_MEMORY_MIN_CONFIDENCE",
                memory.get("min_confidence"),
                DEFAULT_MEMORY_MIN_CONFIDENCE,
            ),
            inject_top_k=_pick_int(
                "ZHARNESS_MEMORY_INJECT_TOP_K",
                memory.get("inject_top_k"),
                DEFAULT_MEMORY_INJECT_TOP_K,
            ),
            search_limit=_pick_int(
                "ZHARNESS_MEMORY_SEARCH_LIMIT",
                memory.get("search_limit"),
                DEFAULT_MEMORY_SEARCH_LIMIT,
            ),
            gate_enabled=_pick_bool(
                "ZHARNESS_MEMORY_GATE_ENABLED",
                memory.get("gate_enabled"),
                DEFAULT_MEMORY_GATE_ENABLED,
            ),
            extraction_enabled=_pick_bool(
                "ZHARNESS_MEMORY_EXTRACTION_ENABLED",
                memory.get("extraction_enabled"),
                DEFAULT_MEMORY_EXTRACTION_ENABLED,
            ),
            extraction_model=_pick(
                "ZHARNESS_MEMORY_EXTRACTION_MODEL",
                memory.get("extraction_model"),
                DEFAULT_MEMORY_EXTRACTION_MODEL,
            ),
            injection_enabled=_pick_bool(
                "ZHARNESS_MEMORY_INJECTION_ENABLED",
                memory.get("injection_enabled"),
                DEFAULT_MEMORY_INJECTION_ENABLED,
            ),
            injection_max_chars=_pick_int(
                "ZHARNESS_MEMORY_INJECTION_MAX_CHARS",
                memory.get("injection_max_chars"),
                DEFAULT_MEMORY_INJECTION_MAX_CHARS,
            ),
        ),
        knowledge=KnowledgeSettings(
            enabled=_pick_bool(
                "ZHARNESS_KNOWLEDGE_ENABLED",
                knowledge.get("enabled"),
                DEFAULT_KNOWLEDGE_ENABLED,
            ),
            embedding=KnowledgeEmbeddingSettings(
                model=_pick(
                    "ZHARNESS_KNOWLEDGE_EMBEDDING_MODEL",
                    knowledge_embedding.get("model"),
                    DEFAULT_KNOWLEDGE_EMBEDDING_MODEL,
                )
                or DEFAULT_KNOWLEDGE_EMBEDDING_MODEL,
                dimensions=_pick_int(
                    "ZHARNESS_KNOWLEDGE_EMBEDDING_DIMENSIONS",
                    knowledge_embedding.get("dimensions"),
                    DEFAULT_KNOWLEDGE_EMBEDDING_DIMENSIONS,
                ),
                batch_size=_pick_int(
                    "ZHARNESS_KNOWLEDGE_EMBEDDING_BATCH_SIZE",
                    knowledge_embedding.get("batch_size"),
                    DEFAULT_KNOWLEDGE_EMBEDDING_BATCH_SIZE,
                ),
                timeout_seconds=_pick_int(
                    "ZHARNESS_KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS",
                    knowledge_embedding.get("timeout_seconds"),
                    DEFAULT_KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS,
                ),
                max_retries=_pick_int(
                    "ZHARNESS_KNOWLEDGE_EMBEDDING_MAX_RETRIES",
                    knowledge_embedding.get("max_retries"),
                    DEFAULT_KNOWLEDGE_EMBEDDING_MAX_RETRIES,
                ),
            ),
            chunking=KnowledgeChunkingSettings(
                size_characters=_pick_int(
                    "ZHARNESS_KNOWLEDGE_CHUNK_SIZE",
                    knowledge_chunking.get("size_characters"),
                    DEFAULT_KNOWLEDGE_CHUNK_SIZE,
                ),
                overlap_characters=_pick_int(
                    "ZHARNESS_KNOWLEDGE_CHUNK_OVERLAP",
                    knowledge_chunking.get("overlap_characters"),
                    DEFAULT_KNOWLEDGE_CHUNK_OVERLAP,
                ),
                add_start_index=_pick_bool(
                    "ZHARNESS_KNOWLEDGE_ADD_START_INDEX",
                    knowledge_chunking.get("add_start_index"),
                    True,
                ),
            ),
            retrieval=KnowledgeRetrievalSettings(
                search_type=_pick(
                    "ZHARNESS_KNOWLEDGE_SEARCH_TYPE",
                    knowledge_retrieval.get("search_type"),
                    DEFAULT_KNOWLEDGE_SEARCH_TYPE,
                )
                or DEFAULT_KNOWLEDGE_SEARCH_TYPE,
                result_limit=_pick_int(
                    "ZHARNESS_KNOWLEDGE_RESULT_LIMIT",
                    knowledge_search_kwargs.get("k"),
                    DEFAULT_KNOWLEDGE_RESULT_LIMIT,
                ),
                fetch_k=_pick_int(
                    "ZHARNESS_KNOWLEDGE_FETCH_K",
                    knowledge_search_kwargs.get("fetch_k"),
                    DEFAULT_KNOWLEDGE_FETCH_K,
                ),
                lambda_mult=_pick_float(
                    "ZHARNESS_KNOWLEDGE_LAMBDA_MULT",
                    knowledge_search_kwargs.get("lambda_mult"),
                    DEFAULT_KNOWLEDGE_LAMBDA_MULT,
                ),
                score_threshold=(
                    _pick_float(
                        "ZHARNESS_KNOWLEDGE_SCORE_THRESHOLD",
                        knowledge_search_kwargs.get("score_threshold"),
                        0.0,
                    )
                    if _env("ZHARNESS_KNOWLEDGE_SCORE_THRESHOLD") is not None
                    or knowledge_search_kwargs.get("score_threshold") is not None
                    else None
                ),
                max_context_chars=_pick_int(
                    "ZHARNESS_KNOWLEDGE_MAX_CONTEXT_CHARS",
                    knowledge_retrieval.get("max_context_chars"),
                    DEFAULT_KNOWLEDGE_MAX_CONTEXT_CHARS,
                ),
                hybrid=KnowledgeHybridSettings(
                    enabled=_pick_bool(
                        "ZHARNESS_KNOWLEDGE_HYBRID_ENABLED",
                        knowledge_hybrid.get("enabled"),
                        DEFAULT_KNOWLEDGE_HYBRID_ENABLED,
                    ),
                    fusion_function=_pick(
                        "ZHARNESS_KNOWLEDGE_FUSION_FUNCTION",
                        knowledge_hybrid.get("fusion_function"),
                        DEFAULT_KNOWLEDGE_FUSION_FUNCTION,
                    )
                    or DEFAULT_KNOWLEDGE_FUSION_FUNCTION,
                    primary_top_k=_pick_int(
                        "ZHARNESS_KNOWLEDGE_PRIMARY_TOP_K",
                        knowledge_hybrid.get("primary_top_k"),
                        DEFAULT_KNOWLEDGE_PRIMARY_TOP_K,
                    ),
                    secondary_top_k=_pick_int(
                        "ZHARNESS_KNOWLEDGE_SECONDARY_TOP_K",
                        knowledge_hybrid.get("secondary_top_k"),
                        DEFAULT_KNOWLEDGE_SECONDARY_TOP_K,
                    ),
                    rrf_k=_pick_float(
                        "ZHARNESS_KNOWLEDGE_RRF_K",
                        knowledge_fusion_parameters.get("rrf_k"),
                        DEFAULT_KNOWLEDGE_RRF_K,
                    ),
                    primary_weight=_pick_float(
                        "ZHARNESS_KNOWLEDGE_PRIMARY_WEIGHT",
                        knowledge_fusion_parameters.get("primary_results_weight"),
                        0.5,
                    ),
                    secondary_weight=_pick_float(
                        "ZHARNESS_KNOWLEDGE_SECONDARY_WEIGHT",
                        knowledge_fusion_parameters.get("secondary_results_weight"),
                        0.5,
                    ),
                ),
            ),
            limits=KnowledgeLimitsSettings(
                max_file_bytes=_pick_int(
                    "ZHARNESS_KNOWLEDGE_MAX_FILE_BYTES",
                    knowledge_limits.get("max_file_bytes"),
                    DEFAULT_KNOWLEDGE_MAX_FILE_BYTES,
                ),
                max_files_per_call=_pick_int(
                    "ZHARNESS_KNOWLEDGE_MAX_FILES_PER_CALL",
                    knowledge_limits.get("max_files_per_call"),
                    DEFAULT_KNOWLEDGE_MAX_FILES_PER_CALL,
                ),
                max_chunks_per_document=_pick_int(
                    "ZHARNESS_KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT",
                    knowledge_limits.get("max_chunks_per_document"),
                    DEFAULT_KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT,
                ),
            ),
        ),
        langsmith=LangsmithSettings(
            tracing=_pick_bool("LANGSMITH_TRACING", langsmith.get("tracing"), False),
            project=_pick("LANGSMITH_PROJECT", langsmith.get("project"), None),
        ),
    )


def get_settings() -> Settings:
    """Return the current settings, reading the environment fresh on every call. / 返回当前配置，每次调用都会重新读取环境变量。"""

    return load_settings()
