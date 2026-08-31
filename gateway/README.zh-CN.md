# Gateway

[English](README.md) | 简体中文

`gateway` 是 ZHarness Next workspace 中为外部接入层预留的 Python 包。

## 当前状态

该包目前尚未实现网关业务能力。现阶段只注册了一个最小命令行入口：

```bash
uv run gateway
```

执行后输出：

```text
Hello from gateway!
```

目前不包含 HTTP 服务、鉴权、请求转发、限流、会话管理或 LangGraph 客户端封装。
实际 Agent API 由仓库根目录 `langgraph.json` 配置的 LangGraph Server 提供。

## 目录结构

```text
gateway/
├── src/gateway/
│   └── __init__.py    # 当前仅包含 CLI main 函数
├── pyproject.toml     # 包配置和 gateway 命令入口
└── README.md
```

## 开发

在仓库根目录安装整个 workspace：

```bash
uv sync --all-packages
```

运行当前入口：

```bash
uv run gateway
```

若后续实现网关，建议由该包承担面向客户端的协议和平台能力，例如鉴权、配额、请求
校验以及对 LangGraph API 的封装；Agent 执行、工作区和 Docker 沙箱逻辑应继续保留在
`zharness` 包中，避免两层职责耦合。
