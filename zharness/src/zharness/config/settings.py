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

DEFAULT_MODEL_NAME = "mimo-v2.5"
"""Default chat model name. / 默认聊天模型名称。"""

DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 2024
DEFAULT_TIMEZONE = "Asia/Shanghai"

DEFAULT_SANDBOX_PROVIDER = "docker"
DEFAULT_SANDBOX_IMAGE = "zharness-sandbox:latest"
DEFAULT_SANDBOX_MEMORY_LIMIT = "512m"
DEFAULT_SANDBOX_NANO_CPUS = 1_000_000_000
DEFAULT_SANDBOX_PIDS_LIMIT = 128
DEFAULT_SANDBOX_IDLE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_SANDBOX_MAX_CONTAINERS = 5
DEFAULT_SANDBOX_CLEANUP_INTERVAL_SECONDS = 5 * 60

DEFAULT_POSTGRES_MANAGED = True
DEFAULT_POSTGRES_USER = "zharness"
DEFAULT_POSTGRES_PASSWORD = "change-me"
DEFAULT_POSTGRES_DB = "zharness"
DEFAULT_POSTGRES_PORT = 5432

DEFAULT_MEMORY_ENABLED = True
DEFAULT_MEMORY_USER_ID = "default"
DEFAULT_MEMORY_MAX_FACTS = 200
DEFAULT_MEMORY_MIN_CONFIDENCE = 0.7
DEFAULT_MEMORY_INJECT_TOP_K = 8
DEFAULT_MEMORY_SEARCH_LIMIT = 10
DEFAULT_MEMORY_GATE_ENABLED = True
DEFAULT_MEMORY_EXTRACTION_ENABLED = True
DEFAULT_MEMORY_EXTRACTION_MODEL = None
DEFAULT_MEMORY_INJECTION_ENABLED = True
DEFAULT_MEMORY_INJECTION_MAX_CHARS = 2000

DEFAULT_KNOWLEDGE_ENABLED = True
DEFAULT_KNOWLEDGE_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_KNOWLEDGE_EMBEDDING_DIMENSIONS = 1024
DEFAULT_KNOWLEDGE_EMBEDDING_BATCH_SIZE = 10
DEFAULT_KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS = 15
DEFAULT_KNOWLEDGE_EMBEDDING_MAX_RETRIES = 2
DEFAULT_KNOWLEDGE_CHUNK_SIZE = 2000
DEFAULT_KNOWLEDGE_CHUNK_OVERLAP = 200
DEFAULT_KNOWLEDGE_SEARCH_TYPE = "similarity"
DEFAULT_KNOWLEDGE_RESULT_LIMIT = 6
DEFAULT_KNOWLEDGE_FETCH_K = 40
DEFAULT_KNOWLEDGE_LAMBDA_MULT = 0.5
DEFAULT_KNOWLEDGE_HYBRID_ENABLED = True
DEFAULT_KNOWLEDGE_FUSION_FUNCTION = "reciprocal_rank_fusion"
DEFAULT_KNOWLEDGE_PRIMARY_TOP_K = 40
DEFAULT_KNOWLEDGE_SECONDARY_TOP_K = 40
DEFAULT_KNOWLEDGE_RRF_K = 60.0
DEFAULT_KNOWLEDGE_MAX_CONTEXT_CHARS = 12_000
DEFAULT_KNOWLEDGE_MAX_FILE_BYTES = 5 * 1024 * 1024
DEFAULT_KNOWLEDGE_MAX_FILES_PER_CALL = 20
DEFAULT_KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT = 1000


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """Chat model provider settings. / 聊天模型提供商配置。"""

    name: str = DEFAULT_MODEL_NAME
    provider: str | None = None
    openai_base_url: str | None = None
    anthropic_base_url: str | None = None
    mimo_base_url: str | None = None


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
    idle_ttl_seconds: int = DEFAULT_SANDBOX_IDLE_TTL_SECONDS
    max_containers: int = DEFAULT_SANDBOX_MAX_CONTAINERS
    cleanup_interval_seconds: int = DEFAULT_SANDBOX_CLEANUP_INTERVAL_SECONDS

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
            idle_ttl_seconds=cfg.idle_ttl_seconds,
            max_containers=cfg.max_containers,
            cleanup_interval_seconds=cfg.cleanup_interval_seconds,
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
class MemorySettings:
    """Long-term memory settings. / 长期记忆配置。

    Facts are stored in PostgreSQL and surfaced to the agent as hidden context
    and through the ``memory_*`` tools. See ``zharness/memory`` for details.

    事实存储于 PostgreSQL，并通过隐藏上下文与 ``memory_*`` 工具呈现给 agent。
    详见 ``zharness/memory``。
    """

    enabled: bool = DEFAULT_MEMORY_ENABLED
    user_id: str = DEFAULT_MEMORY_USER_ID
    max_facts: int = DEFAULT_MEMORY_MAX_FACTS
    min_confidence: float = DEFAULT_MEMORY_MIN_CONFIDENCE
    inject_top_k: int = DEFAULT_MEMORY_INJECT_TOP_K
    search_limit: int = DEFAULT_MEMORY_SEARCH_LIMIT
    gate_enabled: bool = DEFAULT_MEMORY_GATE_ENABLED
    extraction_enabled: bool = DEFAULT_MEMORY_EXTRACTION_ENABLED
    extraction_model: str | None = DEFAULT_MEMORY_EXTRACTION_MODEL
    injection_enabled: bool = DEFAULT_MEMORY_INJECTION_ENABLED
    injection_max_chars: int = DEFAULT_MEMORY_INJECTION_MAX_CHARS


@dataclass(frozen=True, slots=True)
class KnowledgeEmbeddingSettings:
    """Embedding configuration for the knowledge base. / 知识库嵌入模型配置。"""

    model: str = DEFAULT_KNOWLEDGE_EMBEDDING_MODEL
    dimensions: int = DEFAULT_KNOWLEDGE_EMBEDDING_DIMENSIONS
    batch_size: int = DEFAULT_KNOWLEDGE_EMBEDDING_BATCH_SIZE
    timeout_seconds: int = DEFAULT_KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_KNOWLEDGE_EMBEDDING_MAX_RETRIES

    def __post_init__(self) -> None:
        """Validate embedding limits. / 校验嵌入模型限制。"""
        if not self.model.strip():
            raise ValueError("knowledge embedding model must not be empty")
        if self.dimensions < 1:
            raise ValueError("knowledge embedding dimensions must be positive")
        if not 1 <= self.batch_size <= 10:
            raise ValueError("knowledge embedding batch size must be between 1 and 10")
        if self.timeout_seconds < 1:
            raise ValueError("knowledge embedding timeout must be positive")
        if self.max_retries < 0:
            raise ValueError("knowledge embedding retries must be non-negative")


@dataclass(frozen=True, slots=True)
class KnowledgeChunkingSettings:
    """Document splitting configuration. / 文档切分配置。"""

    size_characters: int = DEFAULT_KNOWLEDGE_CHUNK_SIZE
    overlap_characters: int = DEFAULT_KNOWLEDGE_CHUNK_OVERLAP
    add_start_index: bool = True

    def __post_init__(self) -> None:
        """Validate chunk boundaries. / 校验切分边界。"""
        if self.size_characters < 1:
            raise ValueError("knowledge chunk size must be positive")
        if not 0 <= self.overlap_characters < self.size_characters:
            raise ValueError(
                "knowledge chunk overlap must be non-negative and smaller than chunk size"
            )


@dataclass(frozen=True, slots=True)
class KnowledgeHybridSettings:
    """PostgreSQL hybrid-search configuration. / PostgreSQL 混合检索配置。"""

    enabled: bool = DEFAULT_KNOWLEDGE_HYBRID_ENABLED
    fusion_function: str = DEFAULT_KNOWLEDGE_FUSION_FUNCTION
    primary_top_k: int = DEFAULT_KNOWLEDGE_PRIMARY_TOP_K
    secondary_top_k: int = DEFAULT_KNOWLEDGE_SECONDARY_TOP_K
    rrf_k: float = DEFAULT_KNOWLEDGE_RRF_K
    primary_weight: float = 0.5
    secondary_weight: float = 0.5

    def __post_init__(self) -> None:
        """Validate hybrid-search configuration. / 校验混合检索配置。"""
        supported = {"reciprocal_rank_fusion", "weighted_sum_ranking"}
        if self.fusion_function not in supported:
            raise ValueError(
                f"unsupported knowledge fusion function {self.fusion_function!r}"
            )
        if self.primary_top_k < 1 or self.secondary_top_k < 1:
            raise ValueError("knowledge hybrid candidate limits must be positive")
        if self.rrf_k <= 0:
            raise ValueError("knowledge RRF k must be positive")
        if self.primary_weight < 0 or self.secondary_weight < 0:
            raise ValueError("knowledge hybrid weights must be non-negative")
        if self.primary_weight + self.secondary_weight <= 0:
            raise ValueError("knowledge hybrid weights must not both be zero")


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalSettings:
    """Retriever selection and result-budget configuration. / 检索器选择与结果预算配置。"""

    search_type: str = DEFAULT_KNOWLEDGE_SEARCH_TYPE
    result_limit: int = DEFAULT_KNOWLEDGE_RESULT_LIMIT
    fetch_k: int = DEFAULT_KNOWLEDGE_FETCH_K
    lambda_mult: float = DEFAULT_KNOWLEDGE_LAMBDA_MULT
    score_threshold: float | None = None
    max_context_chars: int = DEFAULT_KNOWLEDGE_MAX_CONTEXT_CHARS
    hybrid: KnowledgeHybridSettings = field(default_factory=KnowledgeHybridSettings)

    def __post_init__(self) -> None:
        """Validate supported LangChain retrieval combinations. / 校验受支持的 LangChain 检索组合。"""
        supported = {"similarity", "similarity_score_threshold", "mmr"}
        if self.search_type not in supported:
            raise ValueError(f"unsupported knowledge search type {self.search_type!r}")
        if self.hybrid.enabled and self.search_type != "similarity":
            raise ValueError("knowledge hybrid search requires search_type=similarity")
        if self.result_limit < 1 or self.fetch_k < 1:
            raise ValueError("knowledge retrieval limits must be positive")
        if not 0 <= self.lambda_mult <= 1:
            raise ValueError("knowledge MMR lambda_mult must be between 0 and 1")
        if self.search_type == "mmr" and self.fetch_k < self.result_limit:
            raise ValueError("knowledge MMR fetch_k must be at least result_limit")
        if self.search_type == "similarity_score_threshold" and (
            self.score_threshold is None or not 0 <= self.score_threshold <= 1
        ):
            raise ValueError(
                "knowledge similarity_score_threshold requires a threshold between 0 and 1"
            )
        if (
            self.search_type != "similarity_score_threshold"
            and self.score_threshold is not None
        ):
            raise ValueError(
                "knowledge score_threshold requires "
                "search_type=similarity_score_threshold"
            )


@dataclass(frozen=True, slots=True)
class KnowledgeLimitsSettings:
    """Knowledge ingestion safety limits. / 知识库导入安全限制。"""

    max_file_bytes: int = DEFAULT_KNOWLEDGE_MAX_FILE_BYTES
    max_files_per_call: int = DEFAULT_KNOWLEDGE_MAX_FILES_PER_CALL
    max_chunks_per_document: int = DEFAULT_KNOWLEDGE_MAX_CHUNKS_PER_DOCUMENT

    def __post_init__(self) -> None:
        """Validate ingestion limits. / 校验导入限制。"""
        if (
            min(
                self.max_file_bytes,
                self.max_files_per_call,
                self.max_chunks_per_document,
            )
            < 1
        ):
            raise ValueError("knowledge ingestion limits must be positive")


@dataclass(frozen=True, slots=True)
class KnowledgeSettings:
    """Session-scoped RAG knowledge-base settings. / 会话级 RAG 知识库配置。"""

    enabled: bool = DEFAULT_KNOWLEDGE_ENABLED
    embedding: KnowledgeEmbeddingSettings = field(
        default_factory=KnowledgeEmbeddingSettings
    )
    chunking: KnowledgeChunkingSettings = field(
        default_factory=KnowledgeChunkingSettings
    )
    retrieval: KnowledgeRetrievalSettings = field(
        default_factory=KnowledgeRetrievalSettings
    )
    limits: KnowledgeLimitsSettings = field(default_factory=KnowledgeLimitsSettings)


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
    timezone: str = DEFAULT_TIMEZONE
    sandbox: SandboxSettings = field(default_factory=SandboxSettings)
    postgres: PostgresSettings = field(default_factory=PostgresSettings)
    skills: SkillsSettings = field(default_factory=SkillsSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    knowledge: KnowledgeSettings = field(default_factory=KnowledgeSettings)
    langsmith: LangsmithSettings = field(default_factory=LangsmithSettings)
