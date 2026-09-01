# ZHarness

[English](README.md) | 简体中文

`zharness` 是 ZHarness Next 的核心 Python 包，负责创建 Lead Agent、暴露工作区工具，
并管理线程级沙箱（默认使用加固的 Docker 容器，也可使用本地文件系统后端）来完成文件
与命令执行。

## 模块结构

```text
src/zharness/
├── agents/
│   └── lead.py              # Lead Agent 及工具、中间件配置
├── config/
│   ├── loader.py            # YAML 加载与环境变量覆盖
│   └── settings.py          # 类型化配置数据类
├── host/
│   └── paths.py             # 数据 home 与线程工作区路径解析
├── models/
│   └── factory.py           # Chat Model 工厂
├── sandbox/
│   ├── base.py              # 沙箱后端公共实现
│   ├── docker.py            # Docker 沙箱实现
│   ├── local.py             # 本地文件系统沙箱实现
│   ├── manager.py           # thread 与沙箱的生命周期映射
│   ├── protocol.py          # 执行、上传和下载结果类型
│   └── workspace.py         # 虚拟 / 到沙箱 /workspace 的适配器
├── server/
│   ├── checkpointer.py      # PostgreSQL 检查点生命周期
│   ├── graph.py             # LangGraph 图入口
│   └── http.py              # 沙箱清理中间件和服务生命周期
├── skills/
│   ├── catalog.py           # 不可变技能目录与延迟搜索
│   ├── constants.py         # 技能挂载路径与环境变量常量
│   ├── describe.py          # describe_skill 工具与技能索引提示词
│   ├── frontmatter.py       # 共享的 SKILL.md frontmatter 解析
│   ├── parser.py            # SKILL.md → Skill 元数据
│   ├── storage.py           # 本地技能目录发现
│   ├── types.py             # Skill、SkillCategory 数据类型
│   └── validation.py        # frontmatter 校验工具
├── tools/
│   ├── execute.py           # Agent 命令执行工具
│   └── workspace.py         # Agent 文件系统工具
└── utils.py                 # 共享格式化与 glob 辅助函数
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
| `execute_command` | 在当前 thread 的沙箱中执行 Shell 命令 |
| `describe_skill` | 获取已安装技能的元数据（存在技能时才注册） |

Agent 同时启用了：

- `TodoListMiddleware`：为多步骤任务维护 Todo 状态。
- `SummarizationMiddleware`：上下文达到 4,000 tokens 时生成摘要，并保留最近 8 条消息。
- `HumanInTheLoopMiddleware`：为 `execute_command` 提供每次运行可选的
  `allow_all` 和 `require_approval` 策略，默认为 `allow_all`。
- `ToolErrorMiddleware`：将工具失败格式化为可供模型修复并重试的信息。
- `ToolRetryMiddleware`：对失败的工具调用最多重试 3 次，并带有受限的退避。

## 配置

### 审批策略

客户端通过 `configurable.approval_strategy` 为每次运行选择 Shell 审批策略。不传时
默认为 `allow_all`：

```python
config = {"configurable": {"approval_strategy": "allow_all"}}
```

使用 `require_approval` 可在每次调用 `execute_command` 前中断，等待用户明确
approve/reject：

```python
config = {"configurable": {"approval_strategy": "require_approval"}}
```

客户端应在同一 thread 的每次运行中携带所选值。该策略只控制人工审批；两种模式下
沙箱、路径、超时和输出限制都会继续生效。

非敏感配置位于本包旁的 `config.yaml`；密钥保留在 `langgraph.json` 加载的 `.env` 中。
`zharness.config.loader` 按以下优先级解析每个值：环境变量 → YAML → 内置默认值。
这样 `ZHARNESS_HOME` 等临时覆盖仍可作为环境变量使用，同时 `config.yaml` 成为主要配置面。

## 模型配置

`server/graph.py` 从 `config.yaml` 的 `model.name`（或 `ZHARNESS_MODEL`）读取模型名称。
模型工厂（`zharness.models.factory`）根据 `model.provider`（或 `ZHARNESS_MODEL_PROVIDER`）
选择的提供商创建聊天模型——`deepseek`、`openai` 或 `anthropic`——temperature 为 `0`，
请求超时为 60 秒，最多重试 3 次。未设置提供商时按模型名推断：`claude*` 使用 Anthropic，
`deepseek*` 使用 DeepSeek，其余使用 OpenAI。API Key 分别从 `DEEPSEEK_API_KEY`、
`OPENAI_API_KEY` 和 `ANTHROPIC_API_KEY` 读取。`model.openai_base_url`（或
`ZHARNESS_OPENAI_BASE_URL`）可覆盖 OpenAI 兼容服务（Ollama、vLLM 等）的端点，
`model.anthropic_base_url` 可覆盖 Anthropic 端点。

最低配置如下：

```yaml
# zharness/config.yaml
model:
  name: deepseek-chat
```

```dotenv
# zharness/.env（仅密钥）
DEEPSEEK_API_KEY=your-api-key
```

其他提供商示例：

```yaml
# zharness/config.yaml
model:
  name: gpt-4o
  provider: openai

# 或 / or
model:
  name: claude-sonnet-4-5
  provider: anthropic
```

## 线程工作区

每个 LangGraph thread 对应一个服务端目录：

```text
<home>/workspaces/<thread_id>/
```

`<home>` 是 `config.yaml` 中的 `home` 键（或 `ZHARNESS_HOME`）；未配置时默认使用当前
工作目录下的 `.zharness`。thread ID 只允许字母、数字、下划线和连字符，最长 128 个字符。

Agent 看到的路径是以 `/` 开始的虚拟路径。例如 `/src/main.py` 会映射到当前 thread
Docker 沙箱内的 `/workspace/src/main.py`。文件工具和 `execute_command` 共用同一个
`BaseSandbox` 后端，因此看到完全相同的文件。适配器会拒绝：

- `..` 路径穿越和 `~` 展开；
- 在需要文件路径时操作虚拟根目录；
- 通过仅支持 UTF-8 的 Agent 工具读取二进制文件。

文件读写、编辑、删除、Glob 和 Grep 都委托给线程级沙箱后端完成。

## 技能

Agent 会从一个技能目录中发现可复用的 `SKILL.md` 技能包。`LocalSkillStorage`
按以下顺序解析该目录：

1. 已设置时的 `ZHARNESS_SKILLS_PATH` 或 `skills.path` YAML 键；
2. 存在时的 `<home>/skills`；
3. 仓库内检入的 `skills/` 目录；
4. 其他情况下的 `<home>/skills`。

技能存放在类别子目录中：`public/`（内置、只读）和 `user/`（可编辑）：

```text
<root>/public/<name>/SKILL.md
<root>/user/<name>/SKILL.md
```

系统提示词只嵌入仅含技能名的 `<skill_index>`。模型先调用 `describe_skill` 获取匹配
技能元数据（描述、允许的工具、位置），如需加载再通过 `read_file` 读取完整
`SKILL.md`。每次调用 `describe_skill` 都会重建目录，因此新安装或编辑的技能会立即生效。

技能目录会以只读方式挂载到每个沙箱的 `/mnt/skills`（Docker 与本地提供商一致）。
Agent 可以读取其中的技能文件，但不能写入、编辑或删除其中的任何内容。

## Docker 沙箱

Lead Agent 在首次运行文件或命令工具时，为每个 LangGraph thread 创建或复用一个
Docker 容器。thread 工作区以读写方式挂载到容器内的 `/workspace`，技能目录以只读
方式挂载到 `/mnt/skills`，容器其余部分受到以下限制：

- 根文件系统只读；
- 默认通过 Docker bridge 网络访问外部网络（可用 `sandbox.docker.network_enabled: false`
  或 `ZHARNESS_SANDBOX_NETWORK=0` 关闭）；
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

## 本地沙箱

在单用户、完全可信的本地环境中，文件工具可以不经过 Docker，直接在宿主文件系统上
运行。设置 `config.yaml` 中的 `sandbox.provider: local`（或
`ZHARNESS_SANDBOX_PROVIDER=local`）即可启用：每个 thread 通过同一套
虚拟 `/` 路径空间与路径安全校验，读写宿主上的本地目录。

沙箱相关配置键：

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `sandbox.provider` | `docker` | 沙箱后端：`docker` 或 `local` |
| `sandbox.docker.image` | `zharness-sandbox:latest` | Docker 镜像名称 |
| `sandbox.docker.memory_limit` | `512m` | 容器内存限制 |
| `sandbox.docker.network_enabled` | `true` | Docker 沙箱网络访问 |
| `sandbox.docker.user` | 服务进程 UID/GID | 容器运行用户，例如 `1000:1000` |
| `home` | `./.zharness` | thread 工作区父目录 |
| `sandbox.local.root` | 无 | 本地沙箱根目录，所有 thread 共享（不设置则为各 thread 独立的 `<home>/workspaces/<thread_id>`） |
| `sandbox.local.allow_host_bash` | `false` | 是否允许本地沙箱执行宿主 bash |
| `skills.path` | 解析出的技能根目录 | 覆盖存放 `SKILL.md` 技能包的目录 |

命令长度上限为 128 KiB，超时参数范围为 1 至 300 秒，保留输出默认最多 1 MiB。
Docker 沙箱文件上传或下载的单文件默认上限为 16 MiB；本地文件操作默认上限为
256 KiB。

服务进程需要访问 Docker Engine。推荐使用 rootless Docker，且不要把 Docker socket
挂载进沙箱容器。

## 生命周期

- Docker 容器名由 thread ID 的 SHA-256 摘要生成；本地沙箱在内存中按 thread ID 管理。
- 复用容器前会校验所属 thread、工作区挂载和安全选项。
- LangGraph thread 删除成功后，自定义 HTTP 中间件会删除对应沙箱：Docker 容器，或
  各 thread 自己的本地工作区。
- 服务正常关闭时会停止所有带 `zharness.sandbox=true` 标签的运行中容器，但保留容器，
  以便服务重启后继续复用；正在运行的本地宿主命令也会被终止。
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

测试覆盖 Agent 工具注册、中间件（含人工审批）、工作区路径隔离、文件系统、Docker 与
本地沙箱、命令执行、技能解析与发现、PostgreSQL 检查点以及 HTTP 生命周期清理。
