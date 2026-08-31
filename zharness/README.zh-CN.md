# ZHarness

[English](README.md) | 简体中文

`zharness` 是 ZHarness Next 的核心 Python 包，负责创建 Lead Agent、暴露工作区工具，
并管理线程级 Docker 文件与命令执行沙箱。

## 模块结构

```text
src/zharness/
├── agents/
│   └── lead.py              # Lead Agent 及工具、中间件配置
├── models/
│   └── factory.py           # Chat Model 工厂
├── sandbox/
│   ├── docker.py            # Docker 沙箱实现
│   ├── manager.py           # thread 与容器的生命周期映射
│   ├── protocol.py          # 执行、上传和下载结果类型
│   ├── sandbox.py           # 沙箱抽象接口
│   └── workspace.py         # 虚拟 / 到沙箱 /workspace 的适配器
├── tools/
│   ├── execute.py           # Agent 命令执行工具
│   └── workspace.py         # Agent 文件系统工具
├── workspace/
│   └── paths.py             # thread 工作区宿主机挂载路径解析
├── graph.py                 # LangGraph 图入口
└── http.py                  # 容器清理中间件和服务生命周期
```

## Lead Agent

`create_lead_agent()` 使用 LangChain `create_agent` 创建名为 `lead_agent` 的智能体。
当前注册以下工具：

| 工具 | 作用 |
| --- | --- |
| `list_workspace` | 列出目录的直接子项和元数据 |
| `read_file` | 分页读取 UTF-8 文本文件 |
| `write_file` | 原子创建或覆盖文本文件 |
| `edit_file` | 精确替换一个或全部文本片段 |
| `delete_path` | 删除文件或目录树 |
| `glob_files` | 使用 Glob 模式查找路径 |
| `grep_files` | 在工作区文本文件中搜索字面字符串 |
| `execute_command` | 在当前 thread 的 Docker 沙箱中执行 Shell 命令 |

Agent 同时启用了：

- `TodoListMiddleware`：为多步骤任务维护 Todo 状态。
- `SummarizationMiddleware`：上下文达到 4,000 tokens 时生成摘要，并保留最近 8 条消息。

## 模型配置

`graph.py` 从 `ZHARNESS_MODEL` 读取模型名称。当前模型工厂使用
`langchain-deepseek` 的 `ChatDeepSeek`，temperature 为 `0`，请求超时为 60 秒，最多
重试 3 次。

最低配置如下：

```dotenv
ZHARNESS_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your-api-key
```

## 线程工作区

每个 LangGraph thread 对应一个服务端目录：

```text
${ZHARNESS_HOME}/workspaces/<thread_id>/
```

若未配置 `ZHARNESS_HOME`，默认使用当前工作目录下的 `.zharness`。thread ID 只允许
字母、数字、下划线和连字符，最长 128 个字符。

Agent 看到的路径是以 `/` 开始的虚拟路径。例如 `/src/main.py` 会映射到当前 thread
Docker 沙箱内的 `/workspace/src/main.py`。文件工具和 `execute_command` 共用同一个
`BaseSandbox` 后端，因此看到完全相同的文件。适配器会拒绝：

- `..` 路径穿越和 `~` 展开；
- 在需要文件路径时操作虚拟根目录；
- 通过仅支持 UTF-8 的 Agent 工具读取二进制文件。

文件读写、编辑、删除、Glob 和 Grep 都委托给线程级沙箱后端完成。

## Docker 沙箱

Lead Agent 在首次运行文件或命令工具时，为每个 LangGraph thread 创建或复用一个
Docker 容器。thread 工作区以读写方式挂载到容器内的 `/workspace`，容器其余部分受到
以下限制：

- 根文件系统只读；
- 可联网访问宿主机网络；
- 丢弃全部 Linux capabilities；
- 启用 `no-new-privileges`；
- `/tmp` 使用带 `nosuid`、`nodev` 和 `noexec` 的 tmpfs；
- 默认限制为 1 CPU、512 MiB 内存和 128 个进程。

在项目根目录构建镜像：

```bash
docker build -f docker/sandbox.Dockerfile -t zharness-sandbox:latest .
```

沙箱镜像包含 Python、uv、Git、GNU Coreutils、Findutils、Grep、Ripgrep、curl、
wget 和 C 编译工具链。容器可联网，可在运行时安装依赖，但根文件系统只读，
依赖需安装到 `/workspace` 才能在容器重建后保留。

沙箱相关环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ZHARNESS_SANDBOX_IMAGE` | `zharness-sandbox:latest` | Docker 镜像名称 |
| `ZHARNESS_SANDBOX_MEMORY` | `512m` | 容器内存限制 |
| `ZHARNESS_SANDBOX_USER` | 服务进程 UID/GID | 容器运行用户，例如 `1000:1000` |
| `ZHARNESS_HOME` | `./.zharness` | thread 工作区所在目录 |

命令长度上限为 128 KiB，超时参数范围为 1 至 300 秒，保留输出默认最多 1 MiB。
沙箱文件上传或下载的单文件默认上限为 16 MiB。

服务进程需要访问 Docker Engine。推荐使用 rootless Docker，且不要把 Docker socket
挂载进沙箱容器。

## 生命周期

- 容器名由 thread ID 的 SHA-256 摘要生成。
- 复用容器前会校验所属 thread、工作区挂载和安全选项。
- LangGraph thread 删除成功后，自定义 HTTP 中间件会删除对应容器。
- 服务正常关闭时会停止所有带 `zharness.sandbox=true` 标签的运行中容器，但保留容器，
  以便服务重启后继续复用。
- `kill -9` 等强制终止无法执行关闭清理；废弃 thread 可由外部 TTL 任务调用
  `DockerSandboxManager.remove_for_thread()` 清理。

## 测试

从仓库根目录执行：

```bash
uv run pytest zharness/tests
```

Docker 集成测试默认跳过，显式启用方式为：

```bash
ZHARNESS_RUN_DOCKER_TESTS=1 uv run pytest zharness/tests/test_docker_integration.py
```

测试覆盖 Agent 工具注册、中间件、工作区路径隔离、文件系统、Docker 沙箱、命令执行
以及 HTTP 生命周期清理。
