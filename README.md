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
- Reasoning and tool calling through DeepSeek, OpenAI, or Anthropic chat models.
- Thread-scoped workspaces with a shared virtual path model across sandbox
  providers.
- Tools for directory listing, file reading and writing, exact edits, deletion,
  glob matching, and text search.
- Pluggable sandbox providers: hardened Docker containers by default, or a
  local filesystem sandbox (`sandbox.provider: local` in
  `zharness/config.yaml`) for trusted local projects.
- Shell approval is selectable per run: `allow_all` executes directly and is
  the default, while `require_approval` interrupts `execute_command` for an
  explicit approve/reject decision.
- Skill discovery: bundled `SKILL.md` packages are exposed through a read-only
  `/mnt/skills` mount and a deferred `describe_skill` tool that keeps the
  system prompt compact.
- Tool failures are formatted for the model and retried automatically before a
  run gives up.
- PostgreSQL-backed checkpoint persistence with an idempotent setup and a
  managed Compose service for local development.
- Todo-based planning for multi-step tasks and automatic summarization of long
  conversations.
- Configurable idle/count-based Docker sandbox cleanup, full thread resource
  cleanup on deletion, and container removal during graceful shutdown.

## Repository Layout

```text
.
├── docker/
│   └── sandbox.Dockerfile    # Agent command-execution environment
├── gateway/                  # Placeholder for a future external gateway
├── scripts/
│   ├── cleanup.py            # Remove sessions, workspaces, and sandboxes
│   ├── server.sh             # Server and PostgreSQL lifecycle helpers
│   └── smoke_server.py       # End-to-end server smoke test
├── skills/                   # Bundled SKILL.md packages (public)
├── zharness/                 # Agent, tools, workspace, and sandbox runtime
│   └── config.yaml           # Non-secret YAML configuration
├── langgraph.json            # LangGraph graph and HTTP application config
├── pyproject.toml            # uv workspace configuration
└── uv.lock                   # Locked Python dependencies
```

Runtime data (thread workspaces, server logs) lives under the configured
`home` directory (the `home` key in `zharness/config.yaml`, or
`ZHARNESS_HOME`), which defaults to `.zharness` in the current working
directory.

## How It Works

1. A client creates a LangGraph thread and sends a message to `lead_agent`.
2. The agent calls workspace tools or the command-execution tool as needed.
3. On the first file or command operation, the server selects the configured
   sandbox provider. Docker is used when `sandbox.provider` is `docker` or unset.
4. The Docker provider creates a container for the thread and mounts
   `<home>/workspaces/<thread_id>` at `/workspace`.
5. The local provider operates directly on the configured `sandbox.local.root`,
   when set, or on the thread workspace otherwise. A configured local root is
   shared by all threads.
6. Later operations reuse the same thread sandbox. Deleting a thread removes
   its Docker container and workspace, or its automatically managed local
   workspace; a shared local root is never deleted.

The `/` path exposed to agent tools is the current thread's virtual workspace
root, not the operating-system root. In the Docker provider it maps to
`/workspace`; in the local provider it maps to the configured host directory.
Installed skills are mounted read-only at `/mnt/skills` and are not part of the
user workspace.

## Sandbox Providers

| Capability | Docker (default) | Local |
| --- | --- | --- |
| Selection | `sandbox.provider: docker` or unset | `sandbox.provider: local` |
| Workspace | One host workspace mounted into one container per thread | `sandbox.local.root`, or one managed workspace per thread |
| File operations | Confined to `/workspace` in the container | Confined by validated paths to the selected host directory |
| Shell commands | Enabled inside the container | Disabled unless `sandbox.local.allow_host_bash: true` |
| Intended use | Default; production and shared environments | Single-user, trusted local development only |

The local provider is not a security boundary equivalent to Docker. Enabling
host bash gives the agent the permissions of the ZHarness server process.

## Requirements

- Python 3.13 or later
- [uv](https://docs.astral.sh/uv/)
- Docker Engine when using the default Docker sandbox
- An API key for your chosen model provider (DeepSeek, OpenAI, or Anthropic)

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

### 3. Configure the server

Non-secret settings live in `zharness/config.yaml`. Copy the committed
template `zharness/.env.example` to `zharness/.env` only for secrets. Define
at least the following:

```yaml
# zharness/config.yaml
model:
  name: deepseek-chat
```

```dotenv
# zharness/.env (secrets only; never commit)
DEEPSEEK_API_KEY=your-api-key
LANGGRAPH_STRICT_MSGPACK=true
```

`langgraph.json` loads `zharness/.env` by default; `zharness/config.yaml` is
read by the Python runtime and by `scripts/server.sh`. A set environment
variable always overrides the matching YAML value, so temporary overrides
(such as `ZHARNESS_HOME`) can still be exported directly.

The server-wide `AsyncPostgresSaver` derives its connection URI from the managed
Compose settings in `zharness/config.yaml`. At startup, the server applies the
idempotent checkpoint migrations and keeps the connection open until shutdown.
`make start` and `make dev` automatically start the PostgreSQL service defined
in `docker-compose.yml` and wait for its health check before starting LangGraph.
The default Compose credentials can be configured with:

```yaml
# zharness/config.yaml
postgres:
  managed: true
  user: zharness
  database: zharness
  port: 5432
```

Keep the managed `postgres.password` in `zharness/.env`
(`ZHARNESS_POSTGRES_PASSWORD`) instead of the YAML file. Set
`postgres.managed: false` when using an externally managed database; an
explicit `ZHARNESS_POSTGRES_URI` is required then and overrides all managed
connection settings.
`make stop` stops the Compose container but retains its named volume. The
database can also be managed independently with `make postgres-start`,
`make postgres-stop`, and `make postgres-logs`.

Use the same LangGraph `thread_id` on subsequent runs to resume its persisted
conversation state. Deleting a thread through the LangGraph API also deletes
its checkpoints. `make clean` does not delete rows from an external PostgreSQL
database; delete threads through the API or apply a separate database retention
policy.

The provider is selected by `model.provider` (or `ZHARNESS_MODEL_PROVIDER`) and
inferred from the model name when null: names starting with `claude` use
Anthropic, names starting with `deepseek` use DeepSeek, and everything else
uses OpenAI. For example:

```yaml
# zharness/config.yaml
model:
  name: qwen3
  provider: openai
  openai_base_url: http://127.0.0.1:11434/v1
```

Optional settings in `zharness/config.yaml`:

| Key | Default | Purpose |
| --- | --- | --- |
| `model.name` | `deepseek-chat` | Chat model name |
| `model.provider` | Inferred from model name | Model provider: `deepseek`, `openai`, or `anthropic` |
| `model.openai_base_url` | None | Base URL for OpenAI-compatible endpoints (Ollama, vLLM, etc.) |
| `model.anthropic_base_url` | None | Base URL override for the Anthropic provider |
| `server.host` | `127.0.0.1` | Server bind host |
| `server.port` | `2024` | Server bind port |
| `home` | `<cwd>/.zharness` | Server-owned data directory |
| `sandbox.provider` | `docker` | Sandbox backend: `docker` or `local` |
| `sandbox.docker.image` | `zharness-sandbox:latest` | Sandbox image name |
| `sandbox.docker.memory_limit` | `512m` | Memory limit per container |
| `sandbox.docker.nano_cpus` | `1000000000` | CPU quota in nanocores |
| `sandbox.docker.pids_limit` | `128` | Process limit per container |
| `sandbox.docker.user` | Server process UID/GID | Container user, for example `1000:1000` |
| `sandbox.docker.network_enabled` | `true` | Docker sandbox network access |
| `sandbox.docker.idle_ttl_seconds` | `86400` | Remove containers idle for this many seconds; `0` disables TTL cleanup |
| `sandbox.docker.max_containers` | `5` | Maximum retained sandbox containers; `0` disables the count limit |
| `sandbox.docker.cleanup_interval_seconds` | `300` | Background sandbox cleanup interval |
| `sandbox.local.root` | Per-thread workspace | Host directory used by every thread with the local provider |
| `sandbox.local.allow_host_bash` | `false` | Allow the local provider to execute host shell commands |
| `skills.path` | `<home>/skills`, then the repo `skills/` | Override the directory that contains installed `SKILL.md` packages |
| `postgres.managed` | `true` | Use the Compose-managed PostgreSQL service |
| `postgres.user` | `zharness` | Managed PostgreSQL user |
| `postgres.database` | `zharness` | Managed PostgreSQL database |
| `postgres.port` | `5432` | Managed PostgreSQL host port |
| `langsmith.tracing` | `false` | Enable LangSmith tracing |
| `langsmith.project` | None | LangSmith project name |

Every key above can be overridden with its `ZHARNESS_*` (or `LANGSMITH_*`)
environment variable. API keys and `LANGSMITH_API_KEY` are always read from the
environment (`.env`).

For trusted local development without Docker, set for example:

```yaml
# zharness/config.yaml
sandbox:
  provider: local
  local:
    root: /absolute/path/to/project
    # Optional and high trust: allow_host_bash: true
```

### 4. Start the development server

```bash
make start
```

The default server address is `http://127.0.0.1:2024`. You can interact with it
through LangGraph Studio or use the LangGraph SDK to create threads and run
`lead_agent`. Use `make logs` to follow the background server logs, `make status`
to inspect its state, and `make stop` to stop it. The bind address and port are
configured with `server.host` and `server.port` in `zharness/config.yaml`.
When the Docker sandbox provider is selected (the default), `make start` also
verifies that Docker is installed, running, and accessible before starting the
server. The check times out after five seconds if Docker is paused or
unresponsive. Use `make dev` instead to run the server in the foreground and
stop it with `Ctrl+C`; it performs the same startup checks.

### 5. Run the smoke test

Keep the server running and execute this command in another terminal:

```bash
uv run python scripts/smoke_server.py
```

With the default provider, the script verifies workspace reads, file writes and
edits, Todo-based task planning, the approval interruption before `execute_command`
runs, and the workspace mount during Docker command execution.

### 6. Clean up runtime data

To remove local Agent Server metadata, managed per-thread workspaces, and
Docker sandbox containers, run the cleanup script from the repository root:

```bash
uv run --package zharness python scripts/cleanup.py --dry-run   # preview
make clean
```

You can limit what gets cleaned with `--sessions`, `--workspaces`, and
`--sandboxes`, add Python/lint caches with `--caches`, or also delete the sandbox
image with `--remove-image`. Use `--dry-run` to preview, and `-y` to skip the
confirmation prompt (required for non-interactive use). PostgreSQL checkpoints
are intentionally excluded; delete their threads through the API or clean the
database separately.

Run `make help` to list all project commands. Use `make clean-dry-run` to preview
the default cleanup through the Makefile.

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

- The model factory supports DeepSeek, OpenAI (including OpenAI-compatible
  endpoints), and Anthropic providers, selected with `model.provider` in
  `zharness/config.yaml` (or `ZHARNESS_MODEL_PROVIDER`).
- `execute_command` defaults to unattended execution. Clients can set
  `configurable.approval_strategy` to `require_approval` to require an explicit
  approve/reject decision for the run.
- Workspace file tools operate on UTF-8 text through the same sandbox as shell
  commands. Docker transfers default to a 16 MiB per-file limit; local file
  operations default to 256 KiB.
- Shell commands may run for at most 300 seconds, with retained output limited
  to 1 MiB by default.
- Docker sandboxes have network access through the host bridge, but the root
  filesystem is read-only, so runtime dependencies must be installed into
  `/workspace` or baked into the sandbox image.
- Skills are mounted read-only at `/mnt/skills`; the agent can read them but
  never writes into that namespace.
- The local sandbox provider is intended for single-user, trusted local
  environments only. Host bash execution is disabled unless
  `sandbox.local.allow_host_bash` is `true`, and enabling it runs commands
  with the ZHarness server process's host permissions.
- `gateway` does not yet implement authentication, forwarding, or business APIs.
