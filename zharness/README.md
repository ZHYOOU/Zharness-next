# ZHarness

English | [简体中文](README.zh-CN.md)

`zharness` is the core Python package of ZHarness Next. It creates the Lead
Agent, exposes workspace tools, and manages thread-scoped Docker sandboxes for
file and command execution.

## Package Layout

```text
src/zharness/
├── agents/
│   └── lead.py              # Lead Agent, tools, and middleware
├── models/
│   └── factory.py           # Chat model factory
├── sandbox/
│   ├── docker.py            # Docker sandbox implementation
│   ├── manager.py           # Thread-to-container lifecycle mapping
│   ├── protocol.py          # Execution and file-transfer result types
│   ├── sandbox.py           # Sandbox abstraction
│   └── workspace.py         # Virtual / to sandbox /workspace adapter
├── tools/
│   ├── execute.py           # Agent command-execution tool
│   └── workspace.py         # Agent filesystem tools
├── workspace/
│   └── paths.py             # Thread workspace host-mount resolution
├── graph.py                 # LangGraph graph entry point
└── http.py                  # Cleanup middleware and server lifespan
```

## Lead Agent

`create_lead_agent()` uses LangChain's `create_agent` to create an agent named
`lead_agent`. The following tools are registered:

| Tool | Purpose |
| --- | --- |
| `list_workspace` | List direct children and metadata for a directory |
| `read_file` | Read a UTF-8 text file with line-based pagination |
| `write_file` | Atomically create or overwrite a text file |
| `edit_file` | Replace one exact text occurrence or all occurrences |
| `delete_path` | Delete a file or directory tree |
| `glob_files` | Find paths with a glob pattern |
| `grep_files` | Search workspace text files for a literal string |
| `execute_command` | Run a shell command in the current thread's Docker sandbox |

The agent also enables:

- `TodoListMiddleware` for tracking multi-step tasks.
- `SummarizationMiddleware`, which summarizes the context at 4,000 tokens while
  retaining the eight most recent messages.

## Model Configuration

`graph.py` reads the model name from `ZHARNESS_MODEL`. The current model factory
uses `ChatDeepSeek` from `langchain-deepseek`, with a temperature of `0`, a
60-second request timeout, and up to three retries.

Minimum configuration:

```dotenv
ZHARNESS_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your-api-key
```

## Thread Workspaces

Each LangGraph thread maps to a server-owned directory:

```text
${ZHARNESS_HOME}/workspaces/<thread_id>/
```

If `ZHARNESS_HOME` is unset, the default is `.zharness` under the current
working directory. Thread IDs may contain letters, digits, underscores, and
hyphens, with a maximum length of 128 characters.

Paths exposed to the agent are virtual paths beginning at `/`. For example,
`/src/main.py` maps to `/workspace/src/main.py` inside the current thread's
Docker sandbox. File tools and `execute_command` therefore share one
`BaseSandbox` backend and see the same files. The adapter rejects:

- `..` path traversal and `~` expansion;
- attempts to operate on the virtual root where a file path is required;
- binary reads through the UTF-8-only Agent tool contract.

Glob and Grep return at most 100 results by default. Writes use a temporary file
followed by `os.replace` to avoid leaving a partially written destination.

## Docker Sandbox

The Lead Agent creates or reuses one sandbox per LangGraph thread when the
first file or command tool runs. The thread workspace is mapped to the virtual
root `/`; the sandbox is constrained as follows:

- read-only root filesystem;
- host-network access;
- all Linux capabilities dropped;
- `no-new-privileges` enabled;
- a `tmpfs` at `/tmp` with `nosuid`, `nodev`, and `noexec`;
- default limits of 1 CPU, 512 MiB of memory, and 128 processes.

Build the image from the repository root:

```bash
docker build -f docker/sandbox.Dockerfile -t zharness-sandbox:latest .
```

The sandbox image includes Python, uv, Git, GNU Coreutils, Findutils, Grep,
Ripgrep, curl, wget, and C build tools. The container has host-network access,
so dependencies can be installed at runtime, but the root filesystem is
read-only, so dependencies must be installed into `/workspace` to survive
container recreation.

## Local Sandbox

For single-user, trusted local deployments the file tools can run directly on
the host filesystem instead of inside Docker. Select the local provider with
`ZHARNESS_SANDBOX_PROVIDER=local`. Each thread then reads and writes a local
directory through the same virtual `/` path space and path-safety checks as the
Docker backend.

Sandbox environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `ZHARNESS_SANDBOX_PROVIDER` | `docker` | Sandbox backend: `docker` or `local` |
| `ZHARNESS_SANDBOX_IMAGE` | `zharness-sandbox:latest` | Docker image name |
| `ZHARNESS_SANDBOX_MEMORY` | `512m` | Container memory limit |
| `ZHARNESS_SANDBOX_USER` | Server process UID/GID | Container user, for example `1000:1000` |
| `ZHARNESS_HOME` | `./.zharness` | Parent directory for thread workspaces |
| `ZHARNESS_LOCAL_ROOT` | None | Local sandbox root shared by every thread (otherwise each thread gets `ZHARNESS_HOME/workspaces/<thread_id>`) |
| `ZHARNESS_ALLOW_HOST_BASH` | Disabled | Enable host bash execution in the local sandbox (`1`/`true`/`yes`) |

Commands are limited to 128 KiB of text and a timeout between 1 and 300 seconds.
Retained output is limited to 1 MiB by default. Sandbox file uploads and
downloads have a default per-file limit of 16 MiB.

The server process requires access to Docker Engine. Rootless Docker is
recommended, and the Docker socket must not be mounted inside sandbox
containers.

## Lifecycle

- Container names are derived from SHA-256 hashes of thread IDs.
- Before reuse, a container's thread label, workspace mount, and security
  options are validated.
- After a LangGraph thread is successfully deleted, the custom HTTP middleware
  removes its container.
- During graceful shutdown, the server stops all running containers labeled
  `zharness.sandbox=true` but retains them for reuse after a restart.
- Forced termination such as `kill -9` cannot run shutdown cleanup. An external
  TTL job can remove abandoned containers with
  `DockerSandboxManager.remove_for_thread()`.

## Testing

Run from the repository root:

```bash
uv run pytest zharness/tests
```

Docker integration tests are skipped by default. Enable them explicitly with:

```bash
ZHARNESS_RUN_DOCKER_TESTS=1 uv run pytest zharness/tests/test_docker_integration.py
```

The test suite covers agent tool registration, middleware, workspace path
isolation, filesystem operations, Docker sandbox behavior, command execution,
and HTTP lifecycle cleanup.
