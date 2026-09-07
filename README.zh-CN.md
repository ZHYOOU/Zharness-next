# ZHarness Next

[English](README.md) | 简体中文

ZHarness Next 是一个面向 AI 编程场景的 Agent 运行底座。它基于 LangGraph
组织智能体，并让每个线程的文件操作和 Shell 命令统一通过可配置的沙箱后端执行。
默认使用加固的线程级 Docker 容器；对于可信的本地项目，也可使用本地文件系统后端。

项目目前处于早期开发阶段：`zharness` 已包含主要运行能力，`gateway` 仍是为后续
网关层预留的包。

## 核心能力

- 基于 LangGraph 和 LangChain 构建 Lead Agent。
- 基于 LangChain Agent Chat UI 的 Next.js 对话前端。
- 通过 MiMo、DeepSeek、OpenAI 或 Anthropic Chat Model 进行推理和工具调用。
- 按 LangGraph `thread_id` 隔离工作区与执行容器。
- 提供目录浏览、文件读写、精确编辑、删除、Glob 和文本搜索工具。
- 使用 DuckDuckGo 网页搜索，返回标题、URL 和摘要，无需 API Key。
- 可插拔的沙箱提供商：默认使用加固的 Docker 容器，也可通过
  `zharness/config.yaml` 中的 `sandbox.provider: local` 切换为本地文件系统沙箱（仅限可信本地项目）。
- 每次运行可选择 Shell 审批策略：默认的 `allow_all` 直接执行；
  `require_approval` 会在 `execute_command` 前中断并等待 approve/reject 决定。
- 技能发现：仓库内的 `SKILL.md` 技能包通过只读的 `/mnt/skills` 挂载点暴露，
  并配合延迟加载的 `describe_skill` 工具，保持系统提示词紧凑。
- 工具失败信息会反馈给模型并自动重试，避免一次失败就结束整轮运行。
- 基于 PostgreSQL 的检查点持久化，包含幂等的建表初始化，本地开发使用托管的
  Compose 服务。
- 使用 Todo 中间件规划多步骤任务，并在上下文过长时自动生成摘要。
- 长期记忆存储在 PostgreSQL 中：每轮结束后自动抽取事实，经确定性写入闸门过滤、
  内容去重、混合驱逐评分限容，并以隐藏上下文与 `memory_search`/`memory_add`/
  `memory_update`/`memory_delete` 工具的形式呈现给 agent。
- 线程级 RAG 知识库由 pgvector 和阿里 `text-embedding-v4` 支撑，支持可配置的
  LangChain 检索策略以及稠密向量/全文检索融合。
- 支持按空闲时间和数量上限自动回收 Docker 沙箱，删除 thread 时完整清理资源，
  服务正常关闭时删除全部沙箱容器。

## 项目结构

```text
.
├── docker/
│   └── sandbox.Dockerfile    # Agent 命令执行环境
├── gateway/                  # 预留的外部网关包
├── frontend/                 # 基于 Agent Chat UI 的 Next.js 前端
├── scripts/
│   ├── cleanup.py            # 清理会话、工作区与沙箱
│   ├── server.sh             # 服务与 PostgreSQL 生命周期辅助脚本
│   └── smoke_server.py       # 服务端到端冒烟验证
├── skills/                   # 仓库内置的 SKILL.md 技能包（public）
├── zharness/                 # Agent、工具、工作区和沙箱实现
│   └── config.yaml           # 非敏感 YAML 配置
├── langgraph.json            # LangGraph 图与 HTTP 应用配置
├── pyproject.toml            # uv workspace 配置
└── uv.lock                   # 锁定的 Python 依赖
```

运行数据（线程工作区、服务日志）位于配置的 `home` 目录（`zharness/config.yaml`
中的 `home` 键，或 `ZHARNESS_HOME`）下，未配置时默认为当前工作目录下的
`.zharness`。

## 工作原理

1. 客户端创建 LangGraph thread，并向 `lead_agent` 提交消息。
2. Agent 根据请求调用工作区工具或命令执行工具。
3. 首次执行文件或命令操作时，服务根据 `sandbox.provider` 选择沙箱提供商。未设置
   时默认使用 Docker。
4. Docker 提供商为 thread 创建专属容器，并将 `<home>/workspaces/<thread_id>`
   挂载到容器内的 `/workspace`。
5. 本地提供商直接操作配置的 `sandbox.local.root`（已配置时，所有 thread 共享），
   否则操作各 thread 自己的工作区。
6. 同一 thread 后续复用同一沙箱；删除 thread 时删除其 Docker 容器和工作区，
   或删除自动管理的本地工作区（共享的本地根目录不会被删除）。

Agent 工具中的 `/` 是当前 thread 的虚拟工作区根目录，并非宿主机根目录。在 Docker
提供商下它映射到 `/workspace`，在本地提供商下映射到配置的宿主目录。已安装的技能以
只读方式挂载到 `/mnt/skills`，不属于用户工作区。

## 沙箱提供商

| 能力 | Docker（默认） | 本地 |
| --- | --- | --- |
| 选择方式 | `sandbox.provider: docker` 或未设置 | `sandbox.provider: local` |
| 工作区 | 每个 thread 一个宿主工作区，挂载进一个容器 | `sandbox.local.root`，或每个 thread 一个托管工作区 |
| 文件操作 | 限制在容器内 `/workspace` | 通过校验路径限制在所选宿主目录内 |
| Shell 命令 | 在容器内启用 | 默认禁用，除非设置 `sandbox.local.allow_host_bash: true` |
| 适用场景 | 默认；生产与共享环境 | 仅限单用户、可信的本地开发 |

本地提供商不是与 Docker 等效的安全边界。启用宿主 bash 后，Agent 将拥有
ZHarness 服务进程的宿主权限。

## 环境要求

- Python 3.13 或更高版本
- [uv](https://docs.astral.sh/uv/)
- Node.js 20 或更高版本及 pnpm
- Nginx，用于提供统一的本地开发入口
- Docker Engine（使用默认 Docker 沙箱时需要）
- 所选模型提供商（MiMo、DeepSeek、OpenAI 或 Anthropic）对应的 API Key

生产或共享环境建议使用 rootless Docker。服务进程需要访问 Docker Engine，但不要
把 Docker socket 挂载进 Agent 沙箱。

## 快速开始

### 1. 安装依赖

在项目根目录执行：

```bash
uv sync --all-packages
```

### 2. 构建沙箱镜像

```bash
docker build -f docker/sandbox.Dockerfile -t zharness-sandbox:latest .
```

### 3. 配置服务

非敏感配置位于 `zharness/config.yaml`；仅把密钥写入 `zharness/.env`。至少配置：

```yaml
# zharness/config.yaml
model:
  name: mimo-v2.5
```

```dotenv
# zharness/.env（仅密钥；切勿提交）
MIMO_API_KEY=your-api-key
LANGGRAPH_STRICT_MSGPACK=true
```

`langgraph.json` 默认读取 `zharness/.env`；`zharness/config.yaml` 由 Python 运行时
和 `scripts/server.sh` 读取。已设置的环境变量始终覆盖 YAML 中对应的值，因此仍可
直接导出 `ZHARNESS_HOME` 等做临时覆盖。

全局 `AsyncPostgresSaver` 默认根据 `zharness/config.yaml` 中的托管 Compose 配置
生成连接 URI。服务启动时会执行幂等的检查点表迁移，并保持数据库连接直到服务关闭。
`make start` 和 `make dev` 会自动启动 `docker-compose.yml` 中的 PostgreSQL，
并等待健康检查通过后再启动 LangGraph。Compose 默认账号可以在 YAML 中配置：

```yaml
# zharness/config.yaml
postgres:
  managed: true
  user: zharness
  database: zharness
  port: 5432
```

托管 `postgres.password` 请保留在 `zharness/.env`（`ZHARNESS_POSTGRES_PASSWORD`），
而不是写入 YAML 文件。使用外部 PostgreSQL 时设置 `postgres.managed: false`，此时必须
提供显式 `ZHARNESS_POSTGRES_URI`；该 URI 会覆盖全部托管连接设置。`make stop` 会
停止前端、后端和 Compose 容器，但保留数据库命名卷。使用 `make backend-stop`
可仅停止后端和托管 PostgreSQL。也可以使用 `make postgres-start`、
`make postgres-stop` 和 `make postgres-logs` 单独管理数据库。

后续运行传入相同的 LangGraph `thread_id` 即可恢复持久化的会话状态。通过 LangGraph
API 删除 thread 时，其检查点也会一并删除。`make clean` 不会删除外部 PostgreSQL
中的数据；需要通过 API 删除 thread，或为数据库单独配置数据保留策略。

提供商由 `model.provider`（或 `ZHARNESS_MODEL_PROVIDER`）选择，为 null 时根据模型名
推断：以 `mimo` 开头的模型使用 MiMo，以 `claude` 开头的模型使用 Anthropic，
以 `deepseek` 开头的模型使用 DeepSeek，其余默认使用 OpenAI。例如：

```yaml
# zharness/config.yaml
model:
  name: qwen3
  provider: openai
  openai_base_url: http://127.0.0.1:11434/v1
```

`zharness/config.yaml` 中的可选配置：

| 键 | 默认值 | 用途 |
| --- | --- | --- |
| `model.name` | `mimo-v2.5` | 聊天模型名称 |
| `model.provider` | 根据模型名推断 | 模型提供商：`mimo`、`deepseek`、`openai` 或 `anthropic` |
| `model.openai_base_url` | 无 | OpenAI 兼容端点的基础地址（Ollama、vLLM 等） |
| `model.anthropic_base_url` | 无 | Anthropic 提供商的基础地址覆盖 |
| `model.mimo_base_url` | `https://api.xiaomimimo.com/v1` | MiMo 提供商的基础地址覆盖 |
| `server.host` | `127.0.0.1` | 服务绑定地址 |
| `server.port` | `2024` | 服务绑定端口 |
| `home` | `<cwd>/.zharness` | 服务器拥有的数据目录 |
| `timezone` | `Asia/Shanghai` | 动态当前日期上下文使用的 IANA 时区 |
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
| `skills.path` | `<home>/skills`，然后仓库 `skills/` | 覆盖存放 `SKILL.md` 技能包的目录 |
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
| `postgres.managed` | `true` | 使用 Compose 托管的 PostgreSQL 服务 |
| `postgres.user` | `zharness` | 托管 PostgreSQL 用户 |
| `postgres.database` | `zharness` | 托管 PostgreSQL 数据库 |
| `postgres.port` | `5432` | 托管 PostgreSQL 宿主端口 |
| `postgres.uri` | 无 | 显式 PostgreSQL 连接 URI；覆盖全部托管设置（请保留在 `.env` 中） |
| `langsmith.tracing` | `false` | 是否启用 LangSmith tracing |
| `langsmith.project` | 无 | LangSmith 项目名称 |

上表每个键都可用对应的 `ZHARNESS_*`（或 `LANGSMITH_*`）环境变量覆盖。API Key 和
`LANGSMITH_API_KEY` 始终从环境（`.env`）读取。

在可信的本地开发环境（不使用 Docker）中，可设置例如：

```yaml
# zharness/config.yaml
sandbox:
  provider: local
  local:
    root: /absolute/path/to/project
    # 可选且需要高度信任：allow_host_bash: true
```

### 4. 启动开发服务与前端

首次使用时安装前端依赖：

```bash
pnpm --dir frontend install --frozen-lockfile
```

使用一个命令同时启动后端和前端：

```bash
make dev
```

`make up` 是同一命令的别名。`make dev` 会先预检服务端口，回收被其他 ZHarness
检出占用的端口，然后一并启动托管 PostgreSQL、后端、前端与 Nginx。所有服务都由
这一条命令统一管理，日志写入 `.zharness/logs/`（后端同时写入
`.zharness/server.log`）。命令会占用当前终端并等待；按 `Ctrl+C` 即可停止全部
服务，也可在另一个终端运行 `make stop`。

如需仅在前台启动后端，使用：

```bash
make backend-dev
```

`make backend-start` 可仅在后台启动后端，原有的 `make start` 继续作为
`make backend-start` 的兼容别名。使用 `make logs` 持续查看后台后端日志，
`make status` 检查运行状态，使用 `make backend-stop` 可仅停止后端和托管
PostgreSQL。`make stop` 会停止包括前端和托管 PostgreSQL 在内的完整开发环境。

默认后端地址为 `http://127.0.0.1:2024`。Nginx 在 `http://localhost:2026`
提供完整开发应用，将页面与热更新流量代理至 `http://127.0.0.1:3000`，并将
`/api/langgraph/*` 代理至后端。前端连接地址可通过 `frontend/.env.local` 覆盖。后端监听地址与端口通过
`zharness/config.yaml` 中的 `server.host` 和 `server.port` 配置。选择 Docker
沙箱（默认配置）时，后端启动前会确认 Docker 已安装、正在运行且当前用户可以访问。
如果 Docker 被暂停或无响应，检查会在五秒后超时退出。

如果 Windows 到 WSL 的 localhost 转发不可用，请使用 `make dev` 输出的网络网关地址，
例如 `http://10.255.255.254:2026`。前端会根据浏览器当前访问来源解析
`/api/langgraph`，因此本地网关与网络网关可以共用同一份配置。

### 5. 运行冒烟验证

保持服务运行，然后在另一个终端执行：

```bash
uv run python scripts/smoke_server.py
```

脚本会验证线程工作区读取、文件写入、文件编辑、Todo 任务规划、`execute_command`
执行前的审批中断，以及 Docker 沙箱命令执行时的工作区挂载。

### 6. 清理运行数据

如需清理本地 Agent Server 元数据、各线程工作区以及 Docker 沙箱容器，请在项目根目录
执行清理脚本：

```bash
uv run --package zharness python scripts/cleanup.py --dry-run   # 预览将删除的内容
make clean
```

可以通过 `--sessions`、`--workspaces`、`--sandboxes` 限定要清理的内容，加
`--caches` 同时清理 Python/静态检查缓存，加 `--remove-image` 一并删除沙箱镜像。
使用 `--dry-run` 预览，`-y` 跳过确认提示（非交互环境必须加上）。PostgreSQL 检查点
不会被该脚本删除；请通过 API 删除对应 thread，或单独清理数据库。

运行 `make help` 可查看全部项目命令；通过 Makefile 预览默认清理内容时，可使用
`make clean-dry-run`。

## 开发与测试

```bash
# 运行单元测试
uv run pytest zharness/tests

# 运行代码检查
uv run ruff check .

# 验证 LangGraph 配置
uv run langgraph validate

# 运行需要 Docker 的集成测试
ZHARNESS_RUN_DOCKER_TESTS=1 uv run pytest zharness/tests/test_docker_integration.py
```

## 子项目文档

- [`zharness`](zharness/README.zh-CN.md)：Agent、工具、工作区和 Docker 沙箱的实现说明。
- [`gateway`](gateway/README.zh-CN.md)：网关包的当前状态和后续开发约定。

## 当前限制

- 模型工厂支持 MiMo、DeepSeek、OpenAI（含 OpenAI 兼容端点）和 Anthropic 提供商，
  通过 `zharness/config.yaml` 中的 `model.provider`（或 `ZHARNESS_MODEL_PROVIDER`）选择。
- `execute_command` 默认无人值守执行；客户端可将
  `configurable.approval_strategy` 设为 `require_approval`，要求本次运行在命令执行前
  等待用户显式 approve/reject。
- 工作区文件工具面向 UTF-8 文本，与 Shell 命令共用同一个沙箱；Docker 沙箱传输的
  默认单文件上限为 16 MiB，本地文件操作默认上限为 256 KiB。
- Shell 命令最长运行 300 秒，保留输出默认最多 1 MiB。
- Docker 沙箱默认通过 bridge 网络访问外部网络；根文件系统只读，因此运行时依赖需安装到
  `/workspace` 或预置在沙箱镜像中。
- 技能以只读方式挂载到 `/mnt/skills`；Agent 可以读取，但不能写入该命名空间。
- 本地沙箱提供商仅适用于单用户、可信的本地环境。宿主 bash 默认禁用，只有显式设置
  `sandbox.local.allow_host_bash: true` 才会启用，启用后命令将拥有 ZHarness 服务进程的
  宿主权限。
- `gateway` 尚未实现鉴权、转发或业务 API。
