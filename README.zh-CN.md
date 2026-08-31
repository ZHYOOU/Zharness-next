# ZHarness Next

[English](README.md) | 简体中文

ZHarness Next 是一个面向 AI 编程场景的 Agent 运行底座。它基于 LangGraph
组织智能体，通过线程级工作区读写文件，并为每个线程提供独立的 Docker
命令执行沙箱。

项目目前处于早期开发阶段：`zharness` 已包含主要运行能力，`gateway` 仍是为后续
网关层预留的包。

## 核心能力

- 基于 LangGraph 和 LangChain 构建 Lead Agent。
- 通过 DeepSeek Chat Model 进行推理和工具调用。
- 按 LangGraph `thread_id` 隔离工作区与执行容器。
- 提供目录浏览、文件读写、精确编辑、删除、Glob 和文本搜索工具。
- 在无网络、只读根文件系统、受资源限制的 Docker 容器中执行 Shell 命令。
- 使用 Todo 中间件规划多步骤任务，并在上下文过长时自动生成摘要。
- 删除线程时清理对应容器，服务正常关闭时停止仍在运行的沙箱。

## 项目结构

```text
.
├── docker/
│   └── sandbox.Dockerfile    # Agent 命令执行环境
├── gateway/                  # 预留的外部网关包
├── scripts/
│   └── smoke_server.py       # 服务端到端冒烟验证
├── zharness/                 # Agent、工具、工作区和沙箱实现
├── langgraph.json            # LangGraph 图与 HTTP 应用配置
├── pyproject.toml            # uv workspace 配置
└── uv.lock                   # 锁定的 Python 依赖
```

## 工作原理

1. 客户端创建 LangGraph thread，并向 `lead_agent` 提交消息。
2. Agent 根据请求调用工作区工具或命令执行工具。
3. 每个 thread 使用 `${ZHARNESS_HOME}/workspaces/<thread_id>` 作为独立工作区。
4. 首次执行命令时，服务创建该 thread 专属的 Docker 容器，并将工作区挂载到
   容器内的 `/workspace`。
5. 同一 thread 后续复用工作区和容器；删除 thread 时同步删除容器。

Agent 工具中的 `/` 是当前 thread 的虚拟工作区根目录，并非宿主机根目录。

## 环境要求

- Python 3.14 或更高版本
- [uv](https://docs.astral.sh/uv/)
- Docker Engine（命令执行功能需要）
- DeepSeek API Key

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

`langgraph.json` 默认读取 `zharness/.env`。创建该文件并至少配置：

```dotenv
ZHARNESS_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your-api-key
ZHARNESS_HOME=/absolute/path/to/zharness-data
```

可选配置：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ZHARNESS_SANDBOX_IMAGE` | `zharness-sandbox:latest` | 沙箱镜像名称 |
| `ZHARNESS_SANDBOX_MEMORY` | `512m` | 单个容器内存限制 |
| `ZHARNESS_SANDBOX_USER` | 服务进程 UID/GID | 容器运行用户，例如 `1000:1000` |
| `LANGSMITH_TRACING` | 未启用 | 是否启用 LangSmith tracing |
| `LANGSMITH_API_KEY` | 无 | LangSmith API Key |
| `LANGSMITH_PROJECT` | 无 | LangSmith 项目名称 |

不要提交包含真实密钥的 `.env` 文件。

### 4. 启动开发服务

```bash
uv run langgraph dev
```

默认服务地址为 `http://127.0.0.1:2024`。可在 LangGraph Studio 中交互，也可以通过
LangGraph SDK 创建 thread 并运行 `lead_agent`。

### 5. 运行冒烟验证

保持服务运行，然后在另一个终端执行：

```bash
uv run python scripts/smoke_server.py
```

脚本会验证线程工作区读取、文件写入、文件编辑以及 Todo 任务规划。

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

- 模型工厂当前只创建 DeepSeek Chat Model。
- 工作区文件工具面向 UTF-8 文本，单文件默认上限为 256 KiB。
- Shell 命令最长运行 300 秒，保留输出默认最多 1 MiB。
- Docker 沙箱没有网络，无法在运行期间下载依赖。
- `gateway` 尚未实现鉴权、转发或业务 API。
