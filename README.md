# ZHarness Next

English | [简体中文](README.zh-CN.md)

ZHarness Next is an agent runtime for AI-assisted coding. It uses LangGraph to
orchestrate agents and runs both file operations and shell commands through a
dedicated Docker sandbox for each thread.

The project is still in an early stage of development. `zharness` contains the
main runtime capabilities, while `gateway` is currently a placeholder for a
future gateway layer.

## Features

- A Lead Agent built with LangGraph and LangChain.
- Reasoning and tool calling through a DeepSeek chat model.
- Workspace and execution-container isolation by LangGraph `thread_id`.
- Tools for directory listing, file reading and writing, exact edits, deletion,
  glob matching, and text search.
- Shell execution in resource-constrained Docker containers with no network and
  a read-only root filesystem.
- Todo-based planning for multi-step tasks and automatic summarization of long
  conversations.
- Container cleanup when a thread is deleted, and graceful sandbox shutdown
  when the server stops.

## Repository Layout

```text
.
├── docker/
│   └── sandbox.Dockerfile    # Agent command-execution environment
├── gateway/                  # Placeholder for a future external gateway
├── scripts/
│   ├── cleanup.py            # Remove sessions, workspaces, and sandboxes
│   └── smoke_server.py       # End-to-end server smoke test
├── zharness/                 # Agent, tools, workspace, and sandbox runtime
├── langgraph.json            # LangGraph graph and HTTP application config
├── pyproject.toml            # uv workspace configuration
└── uv.lock                   # Locked Python dependencies
```

## How It Works

1. A client creates a LangGraph thread and sends a message to `lead_agent`.
2. The agent calls workspace tools or the command-execution tool as needed.
3. Each thread uses `${ZHARNESS_HOME}/workspaces/<thread_id>` as its isolated
   workspace.
4. On the first file or command operation, the server creates a Docker container
   for that thread and mounts the workspace at `/workspace`.
5. Later operations in the same thread reuse the workspace and container.
   Deleting the thread also removes its container.

The `/` path exposed to agent tools is the current thread's virtual workspace
root, not the host filesystem root.

## Requirements

- Python 3.14 or later
- [uv](https://docs.astral.sh/uv/)
- Docker Engine for file and command execution
- A DeepSeek API key

Rootless Docker is recommended in production and shared environments. The
server process needs access to Docker Engine, but the Docker socket must not be
mounted inside agent sandboxes.

## Quick Start

### 1. Install dependencies

Run from the repository root:

```bash
uv sync --all-packages
```

### 2. Build the sandbox image

```bash
docker build -f docker/sandbox.Dockerfile -t zharness-sandbox:latest .
```

### 3. Configure environment variables

`langgraph.json` loads `zharness/.env` by default. Create that file and define at
least the following variables:

```dotenv
ZHARNESS_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your-api-key
ZHARNESS_HOME=/absolute/path/to/zharness-data
```

Optional settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ZHARNESS_SANDBOX_IMAGE` | `zharness-sandbox:latest` | Sandbox image name |
| `ZHARNESS_SANDBOX_MEMORY` | `512m` | Memory limit per container |
| `ZHARNESS_SANDBOX_USER` | Server process UID/GID | Container user, for example `1000:1000` |
| `LANGSMITH_TRACING` | Disabled | Enable LangSmith tracing |
| `LANGSMITH_API_KEY` | None | LangSmith API key |
| `LANGSMITH_PROJECT` | None | LangSmith project name |

Do not commit `.env` files containing real credentials.

### 4. Start the development server

```bash
uv run langgraph dev
```

The default server address is `http://127.0.0.1:2024`. You can interact with it
through LangGraph Studio or use the LangGraph SDK to create threads and run
`lead_agent`.

### 5. Run the smoke test

Keep the server running and execute this command in another terminal:

```bash
uv run python scripts/smoke_server.py
```

The script verifies workspace reads, file writes and edits, Todo-based task
planning, Docker sandbox command execution, and the workspace mount.

### 6. Clean up runtime data

To reset all runtime state — LangGraph session history, per-thread workspaces,
and the Docker sandbox containers — run the cleanup script from the repository
root:

```bash
uv run --package zharness python scripts/cleanup.py --dry-run   # preview
uv run --package zharness python scripts/cleanup.py -y          # apply
```

You can limit what gets cleaned with `--sessions`, `--workspaces`, and
`--sandboxes`, add Python/lint caches with `--caches`, or also delete the sandbox
image with `--remove-image`. Use `--dry-run` to preview, and `-y` to skip the
confirmation prompt (required for non-interactive use). The server recreates all
of this state on demand, so it is safe to run while the server is stopped.

## Development and Testing

```bash
# Run unit tests
uv run pytest zharness/tests

# Run lint checks
uv run ruff check .

# Validate the LangGraph configuration
uv run langgraph validate

# Run Docker integration tests
ZHARNESS_RUN_DOCKER_TESTS=1 uv run pytest zharness/tests/test_docker_integration.py
```

## Package Documentation

- [`zharness`](zharness/README.md): implementation details for the agent, tools,
  workspace, and Docker sandbox.
- [`gateway`](gateway/README.md): current status and development guidance for the
  gateway package.

## Current Limitations

- The model factory currently creates only a DeepSeek chat model.
- Workspace file tools operate on UTF-8 text through the same Docker sandbox as
  shell commands. Sandbox transfers have a default 16 MiB per-file limit.
- Shell commands may run for at most 300 seconds, with retained output limited
  to 1 MiB by default.
- Docker sandboxes have no network access and cannot download dependencies at
  runtime.
- `gateway` does not yet implement authentication, forwarding, or business APIs.
