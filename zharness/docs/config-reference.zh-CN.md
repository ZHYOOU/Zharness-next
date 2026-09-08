# ZHarness 配置参考

[English](config-reference.md) | 简体中文

非敏感配置位于 `zharness/config.yaml`；密钥（API Key、托管 PostgreSQL 密码、显式
PostgreSQL URI）保留在 `zharness/.env` 中——`langgraph.json` 会加载它，
`scripts/server.sh` 会将其传给 Docker Compose。下表每个键都可用对应的 `ZHARNESS_*`
（或 `LANGSMITH_*`）环境变量覆盖；已设置的环境变量始终优先于 YAML 中对应的值。
API Key 和 `LANGSMITH_API_KEY` 始终从环境（`.env`）读取。

## 聊天模型

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `model.name` | `mimo-v2.5` | 聊天模型名称 |
| `model.provider` | 根据模型名推断 | 模型提供商：`mimo`、`deepseek`、`openai` 或 `anthropic` |
| `model.openai_base_url` | 无 | OpenAI 兼容端点的基础地址（Ollama、vLLM 等） |
| `model.anthropic_base_url` | 无 | Anthropic 提供商的基础地址覆盖 |
| `model.mimo_base_url` | `https://api.xiaomimimo.com/v1` | MiMo 提供商的基础地址覆盖 |

## Token 用量

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `token_usage.enabled` | `true` | 收集主 Agent 与子 Agent 模型调用中提供商返回的输入、输出和总 token 数 |

可设置 `ZHARNESS_TOKEN_USAGE_ENABLED=false` 关闭统计 Middleware。启用后，每条
`AIMessage` 会保留提供商返回的 `usage_metadata`；委派调用的用量会归并到父级委派消息，
因此对唯一 AI 消息求和即可得到完整的会话用量。

## 服务与运行时

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `server.host` | `127.0.0.1` | 服务绑定地址 |
| `server.port` | `2024` | 服务绑定端口 |
| `home` | `<cwd>/.zharness` | 服务器拥有的数据目录 |
| `timezone` | `Asia/Shanghai` | 动态当前日期上下文使用的 IANA 时区 |

## 沙箱

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `sandbox.provider` | `docker` | 沙箱后端：`docker` 或 `local` |
| `sandbox.docker.image` | `zharness-sandbox:latest` | 沙箱镜像名称 |
| `sandbox.docker.memory_limit` | `512m` | 单个容器内存限制 |
| `sandbox.docker.nano_cpus` | `1000000000` | CPU 配额（纳核） |
| `sandbox.docker.pids_limit` | `128` | 每容器进程数上限 |
| `sandbox.docker.user` | 服务进程 UID/GID | 容器运行用户，例如 `1000:1000` |
| `sandbox.docker.network_enabled` | `true` | Docker 沙箱网络访问 |
| `sandbox.docker.idle_ttl_seconds` | `86400` | 容器空闲回收秒数；`0` 表示禁用 TTL 清理 |
| `sandbox.docker.max_containers` | `5` | 最多保留的沙箱容器数；`0` 表示禁用数量限制 |
| `sandbox.docker.cleanup_interval_seconds` | `300` | 后台沙箱清理周期（秒） |
| `sandbox.local.root` | 各 thread 自己的工作区 | 本地提供商下所有 thread 共享的宿主目录 |
| `sandbox.local.allow_host_bash` | `false` | 允许本地提供商执行宿主 Shell 命令 |

## 技能

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `skills.path` | `<home>/skills`，然后仓库 `skills/` | 覆盖存放 `SKILL.md` 技能包的目录 |

## 长期记忆

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `memory.enabled` | `true` | 抽取、注入与记忆工具的总开关 |
| `memory.user_id` | `default` | 存储与召回记忆的单用户身份标识 |
| `memory.max_facts` | `200` | 保留事实的容量上限；超出时驱逐评分最低的事实 |
| `memory.min_confidence` | `0.7` | 抽取事实置信度低于该阈值时不入库 |
| `memory.inject_top_k` | `8` | 作为隐藏上下文注入的顶级事实数量 |
| `memory.search_limit` | `10` | `memory_search` 的默认结果条数 |
| `memory.gate_enabled` | `true` | 是否强制执行确定性写入闸门 |
| `memory.extraction_enabled` | `true` | 每轮结束后是否自动抽取记忆 |
| `memory.extraction_model` | 无 | 抽取专用模型名称；null 时复用主模型 |
| `memory.injection_enabled` | `true` | 每次主模型调用是否注入记忆上下文 |
| `memory.injection_max_chars` | `2000` | 注入记忆块的最大字符数 |

## 线程标题

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `title.enabled` | `true` | 自动线程标题生成的总开关 |
| `title.max_words` | `6` | 生成标题的最大单词数 |
| `title.max_chars` | `60` | 标题的最大字符数 |
| `title.model_name` | 无 | 标题生成的专用模型名；null 时使用本地后备标题 |
| `title.prompt_template` | 内置模板 | 标题生成的提示词模板 |

## 知识库

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `knowledge.enabled` | `true` | RAG 知识库总开关 |
| `knowledge.embedding.model` | `text-embedding-v4` | 嵌入模型名称 |
| `knowledge.embedding.dimensions` | `1024` | 嵌入向量维度 |
| `knowledge.embedding.batch_size` | `10` | 嵌入请求批大小 |
| `knowledge.embedding.timeout_seconds` | `15` | 嵌入请求超时（秒） |
| `knowledge.embedding.max_retries` | `2` | 嵌入请求重试次数 |
| `knowledge.chunking.size_characters` | `2000` | 文档切分块大小（字符） |
| `knowledge.chunking.overlap_characters` | `200` | 切分块重叠（字符） |
| `knowledge.retrieval.search_type` | `similarity` | LangChain 检索类型：`similarity`、`similarity_score_threshold` 或 `mmr` |
| `knowledge.retrieval.search_kwargs.k` | `6` | 检索返回的结果数 |
| `knowledge.retrieval.search_kwargs.fetch_k` | `40` | MMR 候选池大小 |
| `knowledge.retrieval.search_kwargs.lambda_mult` | `0.5` | MMR 在相关性与多样性之间的平衡 |
| `knowledge.retrieval.search_kwargs.score_threshold` | 无 | `similarity_score_threshold` 的分数阈值 |
| `knowledge.retrieval.hybrid.enabled` | `true` | 是否启用稠密/全文混合检索 |
| `knowledge.retrieval.hybrid.fusion_function` | `reciprocal_rank_fusion` | 融合函数：`reciprocal_rank_fusion` 或 `weighted_sum_ranking` |
| `knowledge.retrieval.max_context_chars` | `12000` | 传给模型的检索上下文最大字符数 |
| `knowledge.limits.max_file_bytes` | `5242880` | 单文件导入上限（字节） |
| `knowledge.limits.max_files_per_call` | `20` | 每次导入调用的文件数上限 |
| `knowledge.limits.max_chunks_per_document` | `1000` | 单文档切分块数上限 |

## PostgreSQL

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `postgres.managed` | `true` | 使用 Compose 托管的 PostgreSQL 服务 |
| `postgres.user` | `zharness` | 托管 PostgreSQL 用户 |
| `postgres.database` | `zharness` | 托管 PostgreSQL 数据库 |
| `postgres.port` | `5432` | 托管 PostgreSQL 宿主端口 |
| `postgres.uri` | 无 | 显式 PostgreSQL 连接 URI；覆盖全部托管设置（请保留在 `.env` 中） |

## LangSmith

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `langsmith.tracing` | `false` | 是否启用 LangSmith tracing |
| `langsmith.project` | 无 | LangSmith 项目名称 |
