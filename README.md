# ZHarness Next

English | [简体中文](README.zh-CN.md)

ZHarness Next is an agent runtime for AI-assisted coding. It uses LangGraph to
orchestrate agents and runs file operations and shell commands through a
configurable sandbox backend. Hardened per-thread Docker containers are the
default; a local filesystem backend is available for trusted local projects.

The project is still in an early stage of development. `zharness` contains the
main runtime capabilities, while `gateway` is currently a placeholder for a
future gateway layer.

## Features

- A Lead Agent built with LangGraph and LangChain.
- Reasoning and tool calling through a DeepSeek chat model.
- Thread-scoped workspaces with a shared virtual path model across sandbox
  providers.
- Tools for directory listing, file reading and writing, exact edits, deletion,
  glob matching, and text search.
- Pluggable sandbox providers: hardened Docker containers by default, or a
  local filesystem sandbox (`ZHARNESS_SANDBOX_PROVIDER=local`) for trusted
  local projects.
- Todo-based planning for multi-step tasks and automatic summarization of long
  conversations.
- Sandbox cleanup when a thread is deleted, and graceful command shutdown when
  the server stops.

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
3. On the first file or command operation, the server selects the configured
   sandbox provider. Docker is used when `ZHARNESS_SANDBOX_PROVIDER` is unset.
4. The Docker provider creates a container for the thread and mounts
   `${ZHARNESS_HOME}/workspaces/<thread_id>` at `/workspace`.
5. The local provider operates directly on `ZHARNESS_LOCAL_ROOT`, when set, or
   on the thread workspace otherwise. A configured local root is shared by all
   threads.
6. Later operations reuse the same thread sandbox. Deleting a thread removes
   its Docker container or its automatically managed local workspace; a shared
   `ZHARNESS_LOCAL_ROOT` is never deleted.

The `/` path exposed to agent tools is the current thread's virtual workspace
root, not the operating-system root. In the Docker provider it maps to
`/workspace`; in the local provider it maps to the configured host directory.

## Sandbox Providers

| Capability | Docker (default) | Local |
| --- | --- | --- |
| Selection | `ZHARNESS_SANDBOX_PROVIDER=docker` or unset | `ZHARNESS_SANDBOX_PROVIDER=local` |
| Workspace | One host workspace mounted into one container per thread | `ZHARNESS_LOCAL_ROOT`, or one managed workspace per thread |
| File operations | Confined to `/workspace` in the container | Confined by validated paths to the selected host directory |
| Shell commands | Enabled inside the container | Disabled unless `ZHARNESS_ALLOW_HOST_BASH=1` |
| Intended use | Default; production and shared environments | Single-user, trusted local development only |

The local provider is not a security boundary equivalent to Docker. Enabling
host bash gives the agent the permissions of the ZHarness server process.

## Requirements

- Python 3.14 or later
- [uv](https://docs.astral.sh/uv/)
- Docker Engine when using the default Docker sandbox
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

### 2. Build the sandbox image (default Docker provider)

```bash
docker build -f docker/sandbox.Dockerfile -t zharness-sandbox:latest .
```

Skip this step only when using the local provider.

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
| `ZHARNESS_SANDBOX_PROVIDER` | `docker` | Sandbox backend: `docker` or `local` |
| `ZHARNESS_SANDBOX_IMAGE` | `zharness-sandbox:latest` | Sandbox image name |
| `ZHARNESS_SANDBOX_MEMORY` | `512m` | Memory limit per container |
| `ZHARNESS_SANDBOX_USER` | Server process UID/GID | Container user, for example `1000:1000` |
| `ZHARNESS_LOCAL_ROOT` | Per-thread workspace | Host directory used by every thread with the local provider |
| `ZHARNESS_ALLOW_HOST_BASH` | Disabled | Allow the local provider to execute host shell commands (`1`, `true`, or `yes`) |
| `LANGSMITH_TRACING` | Disabled | Enable LangSmith tracing |
| `LANGSMITH_API_KEY` | None | LangSmith API key |
| `LANGSMITH_PROJECT` | None | LangSmith project name |

Do not commit `.env` files containing real credentials.

For trusted local development without Docker, add for example:

```dotenv
ZHARNESS_SANDBOX_PROVIDER=local
ZHARNESS_LOCAL_ROOT=/absolute/path/to/project
# Optional and high trust: ZHARNESS_ALLOW_HOST_BASH=1
```

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

With the default provider, the script verifies workspace reads, file writes and
edits, Todo-based task planning, Docker command execution, and the workspace
mount.

### 6. Clean up runtime data

To reset runtime state — LangGraph session history, managed per-thread
workspaces, and Docker sandbox containers — run the cleanup script from the
repository root:

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
  workspace, and sandbox providers.
- [`gateway`](gateway/README.md): current status and development guidance for the
  gateway package.

## Current Limitations

- The model factory currently creates only a DeepSeek chat model.
- Workspace file tools operate on UTF-8 text through the same sandbox as shell
  commands. Docker transfers default to a 16 MiB per-file limit; local file
  operations default to 256 KiB.
- Shell commands may run for at most 300 seconds, with retained output limited
  to 1 MiB by default.
- Docker sandboxes have network access through the host bridge, but the root
  filesystem is read-only, so runtime dependencies must be installed into
  `/workspace` or baked into the sandbox image.
- The local sandbox provider is intended for single-user, trusted local
  environments only. Host bash execution is disabled unless
  `ZHARNESS_ALLOW_HOST_BASH=1` is set explicitly, and enabling it runs commands
  with the ZHarness server process's host permissions.
- `gateway` does not yet implement authentication, forwarding, or business APIs.
