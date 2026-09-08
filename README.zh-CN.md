# ZHarness Next

[English](README.md) | 简体中文

ZHarness Next 是一个面向 AI 编程场景的 Agent 运行底座。它基于 LangGraph
组织智能体，并让每个线程的文件操作和 Shell 命令统一通过可配置的沙箱后端执行。
默认使用加固的线程级 Docker 容器；对于可信的本地项目，也可使用本地文件系统后端。

项目目前处于早期开发阶段：`zharness` 已包含主要运行能力，`gateway` 仍是为后续
网关层预留的包。

## 前端预览

![新对话页面](docs/img.png)

## 核心能力

- 基于 LangGraph 和 LangChain 构建 Lead Agent。
- 基于 LangChain Agent Chat UI 的 Next.js 对话前端。
- 通过 MiMo、DeepSeek、OpenAI 或 Anthropic Chat Model 进行推理和工具调用。
- 按 LangGraph `thread_id` 隔离工作区与执行容器，并在不同沙箱提供商之间共享同一套
  虚拟路径模型。
- 可选的 Token 用量统计（`token_usage.enabled`）：每条 AI 消息保留提供商返回的输入、
  输出与总 token 数，并将委托的子 Agent 用量合并到发起调度的消息上，因此对去重的
  AI 消息求和即可得到完整会话总量。该统计会显示在对话界面与设置对话框中。
- 提供目录浏览、文件读写、精确编辑、删除、Glob 和文本搜索工具。
- 使用 DuckDuckGo 网页搜索，返回标题、URL 和摘要，无需 API Key。
- 可插拔的沙箱提供商：默认使用加固的 Docker 容器，也可通过
  `zharness/config.yaml` 中的 `sandbox.provider: local` 切换为本地文件系统沙箱（仅限可信本地项目）。
- 每次运行可选择 Shell 审批策略：默认的 `allow_all` 直接执行；
  `require_approval` 会在 `execute_command` 前中断并等待 approve/reject 决定。
- 技能发现：仓库内的 `SKILL.md` 技能包通过只读的 `/mnt/skills` 挂载点暴露，
  并配合延迟加载的 `describe_skill` 工具，保持系统提示词紧凑。
- 工具失败使用稳定错误码且不暴露内部异常细节。只有只读工具会自动重试；文件、
  命令、知识库和记忆写入等带副作用操作不会被重试中间件重复执行。
- 基于 PostgreSQL 的检查点持久化，包含幂等的建表初始化，本地开发使用托管的
  Compose 服务。
- 使用 Todo 中间件规划多步骤任务，并在上下文过长时自动生成摘要。
- 自动生成线程标题：首轮完整交互后，Agent 将 `title` 写入线程状态，默认由首条
  用户消息在本地派生，也可通过 `title.model_name` 使用专用模型生成。
- 长期记忆存储在 PostgreSQL 中：每轮结束后在独立后台任务中自动抽取事实，经确定性
  写入闸门过滤、内容去重、混合驱逐评分限容，并以隐藏上下文与
  `memory_search`/`memory_add`/`memory_update`/`memory_delete` 工具及基于
  `/memory*` HTTP 接口的 Web 管理界面的形式呈现给用户。
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
├── nginx/                    # 本地开发反向代理配置
├── scripts/
│   ├── cleanup.py            # 清理会话、工作区与沙箱
│   ├── dev.sh                # `make dev` 统一启动/停止脚本
│   ├── server.sh             # 服务与 PostgreSQL 生命周期辅助脚本
│   ├── smoke_server.py       # 服务端到端冒烟验证
│   └── trace_url.py          # 解析配置的 LangSmith 项目 URL
├── skills/                   # 仓库内置的 SKILL.md 技能包（public）
├── zharness/                 # Agent、工具、工作区和沙箱实现
│   └── config.yaml           # 非敏感 YAML 配置
├── docs/                     # 项目文档与截图
├── docker-compose.yml        # 本地开发使用的托管 PostgreSQL
├── langgraph.json            # LangGraph 图与 HTTP 应用配置
├── Makefile                  # 项目命令别名（见 `make help`）
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

### 2. 构建沙箱镜像（默认 Docker 提供商）

```bash
docker build -f docker/sandbox.Dockerfile -t zharness-sandbox:latest .
```

仅在使用本地提供商时才需要跳过此步骤。

### 3. 配置服务

非敏感配置位于 `zharness/config.yaml`；仅把密钥写入 `zharness/.env`。请将已提交的
模板 `zharness/.env.example` 复制为 `zharness/.env`（仅用于存放密钥）。至少配置：

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

完整可选配置见
[配置参考](zharness/docs/config-reference.zh-CN.md)。其中每个键都可用对应的
`ZHARNESS_*`（或 `LANGSMITH_*`）环境变量覆盖。API Key 和 `LANGSMITH_API_KEY`
始终从环境（`.env`）读取。

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

启动横幅还会输出运行中后端的 LangSmith Studio 地址；启用 tracing 时，还会输出
配置的 LangSmith 项目 traces 的直接链接。

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
- 长期记忆与检查点共用同一个 PostgreSQL 数据库。抽取在每轮结束后作为进程内的
  后台任务按 thread 入队执行，因此不会阻塞本轮返回；排队的快照会被合并，每份快照
  有 60 秒预算，服务关闭时最多等待 5 秒排空队列，然后取消剩余任务。设置专用
  `memory.extraction_model` 或关闭抽取可将 token 成本保持在可预期范围。
- `gateway` 尚未实现鉴权、转发或业务 API。
