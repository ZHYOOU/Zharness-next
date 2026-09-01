# ZHarness Next

[English](README.md) | 简体中文

ZHarness Next 是一个面向 AI 编程场景的 Agent 运行底座。它基于 LangGraph
组织智能体，并让每个线程的文件操作和 Shell 命令统一通过可配置的沙箱后端执行。
默认使用加固的线程级 Docker 容器；对于可信的本地项目，也可使用本地文件系统后端。

项目目前处于早期开发阶段：`zharness` 已包含主要运行能力，`gateway` 仍是为后续
网关层预留的包。

## 核心能力

- 基于 LangGraph 和 LangChain 构建 Lead Agent。
- 通过 DeepSeek、OpenAI 或 Anthropic Chat Model 进行推理和工具调用。
- 按 LangGraph `thread_id` 隔离工作区与执行容器。
- 提供目录浏览、文件读写、精确编辑、删除、Glob 和文本搜索工具。
- 可插拔的沙箱提供商：默认使用加固的 Docker 容器，也可通过
  `ZHARNESS_SANDBOX_PROVIDER=local` 切换为本地文件系统沙箱（仅限可信本地项目）。
- Shell 命令执行前需要人工审批（`execute_command` 会中断运行，等待显式的
  approve/reject 决定）。
- 技能发现：仓库内的 `SKILL.md` 技能包通过只读的 `/mnt/skills` 挂载点暴露，
  并配合延迟加载的 `describe_skill` 工具，保持系统提示词紧凑。
- 工具失败信息会反馈给模型并自动重试，避免一次失败就结束整轮运行。
- 基于 PostgreSQL 的检查点持久化，包含幂等的建表初始化，本地开发使用托管的
  Compose 服务。
- 使用 Todo 中间件规划多步骤任务，并在上下文过长时自动生成摘要。
- 删除线程时清理对应容器，服务正常关闭时停止仍在运行的沙箱。

## 项目结构

```text
.
├── docker/
│   └── sandbox.Dockerfile    # Agent 命令执行环境
├── gateway/                  # 预留的外部网关包
├── scripts/
│   ├── cleanup.py            # 清理会话、工作区与沙箱
│   ├── server.sh             # 服务与 PostgreSQL 生命周期辅助脚本
│   └── smoke_server.py       # 服务端到端冒烟验证
├── skills/                   # 仓库内置的 SKILL.md 技能包（public）
├── zharness/                 # Agent、工具、工作区和沙箱实现
├── langgraph.json            # LangGraph 图与 HTTP 应用配置
├── pyproject.toml            # uv workspace 配置
└── uv.lock                   # 锁定的 Python 依赖
```

运行数据（线程工作区、服务日志）位于 `ZHARNESS_HOME` 下，未配置时默认为当前
工作目录下的 `.zharness`。

## 工作原理

1. 客户端创建 LangGraph thread，并向 `lead_agent` 提交消息。
2. Agent 根据请求调用工作区工具或命令执行工具。
3. 首次执行文件或命令操作时，服务根据 `ZHARNESS_SANDBOX_PROVIDER` 选择沙箱
   提供商。未设置时默认使用 Docker。
4. Docker 提供商为 thread 创建专属容器，并将 `${ZHARNESS_HOME}/workspaces/<thread_id>`
   挂载到容器内的 `/workspace`。
5. 本地提供商直接操作 `ZHARNESS_LOCAL_ROOT`（已配置时，所有 thread 共享），
   否则操作各 thread 自己的工作区。
6. 同一 thread 后续复用同一沙箱；删除 thread 时删除其 Docker 容器或自动管理的
   本地工作区（共享的 `ZHARNESS_LOCAL_ROOT` 不会被删除）。

Agent 工具中的 `/` 是当前 thread 的虚拟工作区根目录，并非宿主机根目录。在 Docker
提供商下它映射到 `/workspace`，在本地提供商下映射到配置的宿主目录。已安装的技能以
只读方式挂载到 `/mnt/skills`，不属于用户工作区。

## 沙箱提供商

| 能力 | Docker（默认） | 本地 |
| --- | --- | --- |
| 选择方式 | `ZHARNESS_SANDBOX_PROVIDER=docker` 或未设置 | `ZHARNESS_SANDBOX_PROVIDER=local` |
| 工作区 | 每个 thread 一个宿主工作区，挂载进一个容器 | `ZHARNESS_LOCAL_ROOT`，或每个 thread 一个托管工作区 |
| 文件操作 | 限制在容器内 `/workspace` | 通过校验路径限制在所选宿主目录内 |
| Shell 命令 | 在容器内启用 | 默认禁用，除非设置 `ZHARNESS_ALLOW_HOST_BASH=1` |
| 适用场景 | 默认；生产与共享环境 | 仅限单用户、可信的本地开发 |

本地提供商不是与 Docker 等效的安全边界。启用宿主 bash 后，Agent 将拥有
ZHarness 服务进程的宿主权限。

## 环境要求

- Python 3.13 或更高版本
- [uv](https://docs.astral.sh/uv/)
- Docker Engine（使用默认 Docker 沙箱时需要）
- 所选模型提供商（DeepSeek、OpenAI 或 Anthropic）对应的 API Key

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

### 3. 配置环境变量

`langgraph.json` 默认读取 `zharness/.env`。请把仓库内的模板 `zharness/.env.example`
复制为 `zharness/.env` 并调整，至少配置以下变量：

```dotenv
ZHARNESS_MODEL=deepseek-chat
ZHARNESS_MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-api-key
ZHARNESS_HOME=/absolute/path/to/zharness-data
LANGGRAPH_STRICT_MSGPACK=true
```

全局 `AsyncPostgresSaver` 默认根据下方的托管 Compose 配置生成连接 URI。服务启动时会
执行幂等的检查点表迁移，并保持数据库连接直到服务关闭。`make start` 和 `make dev` 会
自动启动 `docker-compose.yml` 中的 PostgreSQL，并等待健康检查通过后再启动 LangGraph。
Compose 默认账号可以通过以下变量配置：

```dotenv
ZHARNESS_POSTGRES_MANAGED=true
ZHARNESS_POSTGRES_USER=zharness
ZHARNESS_POSTGRES_PASSWORD=change-me
ZHARNESS_POSTGRES_DB=zharness
ZHARNESS_POSTGRES_PORT=5432
```

使用外部 PostgreSQL 时设置 `ZHARNESS_POSTGRES_MANAGED=false`，此时必须提供显式
`ZHARNESS_POSTGRES_URI`；该 URI 会覆盖全部托管连接设置。`make stop` 会停止 Compose
容器，但保留数据库命名卷。也可以
使用 `make postgres-start`、`make postgres-stop` 和
`make postgres-logs` 单独管理数据库。

后续运行传入相同的 LangGraph `thread_id` 即可恢复持久化的会话状态。通过 LangGraph
API 删除 thread 时，其检查点也会一并删除。`make clean` 不会删除外部 PostgreSQL
中的数据；需要通过 API 删除 thread，或为数据库单独配置数据保留策略。

提供商由 `ZHARNESS_MODEL_PROVIDER` 选择，未设置时根据模型名推断：以 `claude`
开头的模型使用 Anthropic，以 `deepseek` 开头的模型使用 DeepSeek，其余默认使用
OpenAI。例如：

```dotenv
# OpenAI
ZHARNESS_MODEL=gpt-4o
OPENAI_API_KEY=your-api-key

# Anthropic
ZHARNESS_MODEL=claude-sonnet-4-5
ANTHROPIC_API_KEY=your-api-key

# OpenAI 兼容端点，例如 Ollama 或 vLLM
ZHARNESS_MODEL=qwen3
ZHARNESS_MODEL_PROVIDER=openai
ZHARNESS_OPENAI_BASE_URL=http://127.0.0.1:11434/v1
OPENAI_API_KEY=not-needed
```

可选配置：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ZHARNESS_SANDBOX_PROVIDER` | `docker` | 沙箱后端：`docker` 或 `local` |
| `ZHARNESS_SANDBOX_IMAGE` | `zharness-sandbox:latest` | 沙箱镜像名称 |
| `ZHARNESS_SANDBOX_MEMORY` | `512m` | 单个容器内存限制 |
| `ZHARNESS_SANDBOX_NETWORK` | 开启 | Docker 沙箱网络访问；设为 `0`、`false` 或 `no` 时关闭 |
| `ZHARNESS_SANDBOX_USER` | 服务进程 UID/GID | 容器运行用户，例如 `1000:1000` |
| `ZHARNESS_LOCAL_ROOT` | 各 thread 自己的工作区 | 本地提供商下所有 thread 共享的宿主目录 |
| `ZHARNESS_ALLOW_HOST_BASH` | 关闭 | 允许本地提供商执行宿主 Shell 命令（`1`、`true` 或 `yes`） |
| `ZHARNESS_SKILLS_PATH` | `<ZHARNESS_HOME>/skills`，然后仓库 `skills/` | 覆盖存放 `SKILL.md` 技能包的目录 |
| `ZHARNESS_MODEL_PROVIDER` | 根据模型名推断 | 模型提供商：`deepseek`、`openai` 或 `anthropic` |
| `ZHARNESS_OPENAI_BASE_URL` | 无 | OpenAI 兼容端点的基础地址（Ollama、vLLM 等） |
| `ZHARNESS_ANTHROPIC_BASE_URL` | 无 | Anthropic 提供商的基础地址覆盖 |
| `LANGSMITH_TRACING` | 未启用 | 是否启用 LangSmith tracing |
| `LANGSMITH_API_KEY` | 无 | LangSmith API Key |
| `LANGSMITH_PROJECT` | 无 | LangSmith 项目名称 |

不要提交包含真实密钥的 `.env` 文件。

在可信的本地开发环境（不使用 Docker）中，可添加例如：

```dotenv
ZHARNESS_SANDBOX_PROVIDER=local
ZHARNESS_LOCAL_ROOT=/absolute/path/to/project
# 可选且需要高度信任：ZHARNESS_ALLOW_HOST_BASH=1
```

### 4. 启动开发服务

```bash
make start
```

默认服务地址为 `http://127.0.0.1:2024`。可在 LangGraph Studio 中交互，也可以通过
LangGraph SDK 创建 thread 并运行 `lead_agent`。使用 `make logs` 持续查看后台日志，
`make status` 检查运行状态，使用 `make stop` 停止服务。可通过
`ZHARNESS_SERVER_HOST` 和 `ZHARNESS_SERVER_PORT` 覆盖监听地址与端口。选择 Docker
沙箱（默认配置）时，`make start` 还会在启动服务前确认 Docker 已安装、正在运行且当前
用户可以访问。如果 Docker 被暂停或无响应，检查会在五秒后超时退出。如需在前台运行，
使用 `make dev`，然后按 `Ctrl+C` 停止；该命令执行相同的启动检查。

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

- 模型工厂支持 DeepSeek、OpenAI（含 OpenAI 兼容端点）和 Anthropic 提供商，
  通过 `ZHARNESS_MODEL_PROVIDER` 选择。
- `execute_command` 每次执行前都会中断运行，等待用户显式批准，因此无法完全
  无人值守地运行。
- 工作区文件工具面向 UTF-8 文本，与 Shell 命令共用同一个沙箱；Docker 沙箱传输的
  默认单文件上限为 16 MiB，本地文件操作默认上限为 256 KiB。
- Shell 命令最长运行 300 秒，保留输出默认最多 1 MiB。
- Docker 沙箱默认通过 bridge 网络访问外部网络；根文件系统只读，因此运行时依赖需安装到
  `/workspace` 或预置在沙箱镜像中。
- 技能以只读方式挂载到 `/mnt/skills`；Agent 可以读取，但不能写入该命名空间。
- 本地沙箱提供商仅适用于单用户、可信的本地环境。宿主 bash 默认禁用，只有显式设置
  `ZHARNESS_ALLOW_HOST_BASH=1` 才会启用，启用后命令将拥有 ZHarness 服务进程的
  宿主权限。
- `gateway` 尚未实现鉴权、转发或业务 API。
