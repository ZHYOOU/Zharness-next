# ZHarness Configuration Reference

English | [简体中文](config-reference.zh-CN.md)

Non-secret settings live in `zharness/config.yaml`; secrets (API keys, the
managed PostgreSQL password, an explicit PostgreSQL URI) stay in `zharness/.env`,
which `langgraph.json` loads and which `scripts/server.sh` feeds to Docker
Compose. Every key below can be overridden with its `ZHARNESS_*` (or
`LANGSMITH_*`) environment variable; a set environment variable always wins over
the matching YAML value. API keys and `LANGSMITH_API_KEY` are always read from
the environment (`.env`).

## Chat Model

| Key | Default | Purpose |
| --- | --- | --- |
| `model.name` | `mimo-v2.5` | Chat model name |
| `model.provider` | Inferred from model name | Model provider: `mimo`, `deepseek`, `openai`, or `anthropic` |
| `model.openai_base_url` | None | Base URL for OpenAI-compatible endpoints (Ollama, vLLM, etc.) |
| `model.anthropic_base_url` | None | Base URL override for the Anthropic provider |
| `model.mimo_base_url` | `https://api.xiaomimimo.com/v1` | Base URL override for the MiMo provider |

## Token Usage

| Key | Default | Purpose |
| --- | --- | --- |
| `token_usage.enabled` | `true` | Collect provider-reported input, output, and total tokens for lead and subagent model calls |

Set `ZHARNESS_TOKEN_USAGE_ENABLED=false` to disable the accounting middleware.
When enabled, each `AIMessage` retains its provider `usage_metadata`; delegated
usage is folded into the parent dispatch message so summing unique AI messages
produces the complete conversation total.

## Server and Runtime

| Key | Default | Purpose |
| --- | --- | --- |
| `server.host` | `127.0.0.1` | Server bind host |
| `server.port` | `2024` | Server bind port |
| `home` | `<cwd>/.zharness` | Server-owned data directory |
| `timezone` | `Asia/Shanghai` | IANA timezone used for the dynamic current-date context |

## Sandbox

| Key | Default | Purpose |
| --- | --- | --- |
| `sandbox.provider` | `docker` | Sandbox backend: `docker` or `local` |
| `sandbox.docker.image` | `zharness-sandbox:latest` | Sandbox image name |
| `sandbox.docker.memory_limit` | `512m` | Memory limit per container |
| `sandbox.docker.nano_cpus` | `1000000000` | CPU quota in nanocores |
| `sandbox.docker.pids_limit` | `128` | Process limit per container |
| `sandbox.docker.user` | Server process UID/GID | Container user, for example `1000:1000` |
| `sandbox.docker.network_enabled` | `true` | Docker sandbox network access |
| `sandbox.docker.idle_ttl_seconds` | `86400` | Remove containers idle for this many seconds; `0` disables TTL cleanup |
| `sandbox.docker.max_containers` | `5` | Maximum retained sandbox containers; `0` disables the count limit |
| `sandbox.docker.cleanup_interval_seconds` | `300` | Background sandbox cleanup interval |
| `sandbox.local.root` | Per-thread workspace | Host directory used by every thread with the local provider |
| `sandbox.local.allow_host_bash` | `false` | Allow the local provider to execute host shell commands |

## Skills

| Key | Default | Purpose |
| --- | --- | --- |
| `skills.path` | `<home>/skills`, then the repo `skills/` | Override the directory that contains installed `SKILL.md` packages |

## Long-Term Memory

| Key | Default | Purpose |
| --- | --- | --- |
| `memory.enabled` | `true` | Master switch for extraction, injection, and the memory tools |
| `memory.user_id` | `default` | Identity of the single user whose memories are stored |
| `memory.max_facts` | `200` | Capacity cap for retained facts; lowest-scoring facts are evicted |
| `memory.min_confidence` | `0.7` | Confidence threshold below which extracted facts are not stored |
| `memory.inject_top_k` | `8` | How many top facts to inject as hidden context |
| `memory.search_limit` | `10` | Default result limit for `memory_search` |
| `memory.gate_enabled` | `true` | Enforce the deterministic write gate |
| `memory.extraction_enabled` | `true` | Auto-extract memories after each completed turn |
| `memory.extraction_model` | None | Dedicated extraction model; reuses the lead model when null |
| `memory.injection_enabled` | `true` | Inject memory context into every lead-agent model call |
| `memory.injection_max_chars` | `2000` | Maximum characters of the injected memory block |

## Thread Titles

| Key | Default | Purpose |
| --- | --- | --- |
| `title.enabled` | `true` | Master switch for automatic thread title generation |
| `title.max_words` | `6` | Maximum words in the generated title |
| `title.max_chars` | `60` | Maximum characters in the title |
| `title.model_name` | None | Dedicated model for title generation; null uses a local fallback |
| `title.prompt_template` | Built-in template | Prompt template for LLM title generation |

## Knowledge Base

| Key | Default | Purpose |
| --- | --- | --- |
| `knowledge.enabled` | `true` | Master switch for the RAG knowledge base |
| `knowledge.embedding.model` | `text-embedding-v4` | Embedding model name |
| `knowledge.embedding.dimensions` | `1024` | Embedding vector dimensions |
| `knowledge.embedding.batch_size` | `10` | Embedding request batch size |
| `knowledge.embedding.timeout_seconds` | `15` | Embedding request timeout in seconds |
| `knowledge.embedding.max_retries` | `2` | Embedding request retries |
| `knowledge.chunking.size_characters` | `2000` | Document chunk size in characters |
| `knowledge.chunking.overlap_characters` | `200` | Chunk overlap in characters |
| `knowledge.retrieval.search_type` | `similarity` | LangChain search type: `similarity`, `similarity_score_threshold`, or `mmr` |
| `knowledge.retrieval.search_kwargs.k` | `6` | Number of retrieved results |
| `knowledge.retrieval.search_kwargs.fetch_k` | `40` | MMR candidate pool size |
| `knowledge.retrieval.search_kwargs.lambda_mult` | `0.5` | MMR balance between relevance and diversity |
| `knowledge.retrieval.search_kwargs.score_threshold` | None | Score threshold for `similarity_score_threshold` |
| `knowledge.retrieval.hybrid.enabled` | `true` | Enable hybrid dense/full-text search |
| `knowledge.retrieval.hybrid.fusion_function` | `reciprocal_rank_fusion` | Fusion function: `reciprocal_rank_fusion` or `weighted_sum_ranking` |
| `knowledge.retrieval.max_context_chars` | `12000` | Maximum characters of retrieved context passed to the model |
| `knowledge.limits.max_file_bytes` | `5242880` | Maximum bytes per ingested file |
| `knowledge.limits.max_files_per_call` | `20` | Maximum files per ingest call |
| `knowledge.limits.max_chunks_per_document` | `1000` | Maximum chunks per document |

## PostgreSQL

| Key | Default | Purpose |
| --- | --- | --- |
| `postgres.managed` | `true` | Use the Compose-managed PostgreSQL service |
| `postgres.user` | `zharness` | Managed PostgreSQL user |
| `postgres.database` | `zharness` | Managed PostgreSQL database |
| `postgres.port` | `5432` | Managed PostgreSQL host port |
| `postgres.uri` | None | Explicit PostgreSQL connection URI; overrides the managed settings (keep it in `.env`) |

## LangSmith

| Key | Default | Purpose |
| --- | --- | --- |
| `langsmith.tracing` | `false` | Enable LangSmith tracing |
| `langsmith.project` | None | LangSmith project name |
