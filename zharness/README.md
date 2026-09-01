# ZHarness

English | [简体中文](README.zh-CN.md)

`zharness` is the core Python package of ZHarness Next. It creates the Lead
Agent, exposes workspace tools, and manages thread-scoped sandboxes (hardened
Docker containers by default, or a local filesystem backend) for file and
command execution.

## Package Layout

```text
src/zharness/
├── agents/
│   └── lead.py              # Lead Agent, tools, and middleware
├── host/
│   └── paths.py             # ZHARNESS_HOME and thread workspace resolution
├── models/
│   └── factory.py           # Chat model factory
├── sandbox/
│   ├── base.py              # Shared sandbox backend implementation
│   ├── docker.py            # Docker sandbox implementation
│   ├── local.py             # Local filesystem sandbox implementation
│   ├── manager.py           # Thread-to-sandbox lifecycle mapping
│   ├── protocol.py          # Execution and file-transfer result types
│   └── workspace.py         # Virtual / to sandbox /workspace adapter
├── server/
│   ├── checkpointer.py      # PostgreSQL-backed checkpoint lifecycle
│   ├── graph.py             # LangGraph graph entry point
│   └── http.py              # Sandbox cleanup middleware and server lifespan
├── skills/
│   ├── catalog.py           # Immutable skill catalog with deferred search
│   ├── constants.py         # Skills mount path and env-var constants
│   ├── describe.py          # describe_skill tool and skill-index prompt
│   ├── frontmatter.py       # Shared SKILL.md frontmatter parsing
│   ├── parser.py            # SKILL.md → Skill metadata
│   ├── storage.py           # Local skills-directory discovery
│   ├── types.py             # Skill, SkillCategory data types
│   └── validation.py        # Frontmatter validation utilities
├── tools/
│   ├── execute.py           # Agent command-execution tool
│   └── workspace.py         # Agent filesystem tools
└── utils.py                 # Shared formatting and glob helpers
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
| `execute_command` | Run a shell command in the current thread's sandbox |
| `describe_skill` | Fetch metadata for installed skills (registered when skills exist) |

The agent also enables:

- `TodoListMiddleware` for tracking multi-step tasks.
- `SummarizationMiddleware`, which summarizes the context at 4,000 tokens while
  retaining the eight most recent messages.
- `HumanInTheLoopMiddleware`, which interrupts the run for an explicit
  approve/reject decision before `execute_command` executes.
- `ToolErrorMiddleware`, which formats tool failures for the model to fix and
  retry.
- `ToolRetryMiddleware`, which retries failed tool calls up to three times with
  bounded backoff.

## Model Configuration

`server/graph.py` reads the model name from `ZHARNESS_MODEL`. The model factory
(`zharness.models.factory`) creates a chat model for the provider selected by
`ZHARNESS_MODEL_PROVIDER` — `deepseek`, `openai`, or `anthropic` — with a
temperature of `0`, a 60-second request timeout, and up to three retries. When
the provider is unset it is inferred from the model name: `claude*` uses
Anthropic, `deepseek*` uses DeepSeek, and anything else uses OpenAI. API keys
are read from `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, and `ANTHROPIC_API_KEY`
respectively. `ZHARNESS_OPENAI_BASE_URL` overrides the endpoint for
OpenAI-compatible services (Ollama, vLLM, etc.), and
`ZHARNESS_ANTHROPIC_BASE_URL` overrides the Anthropic endpoint.

Minimum configuration:

```dotenv
ZHARNESS_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your-api-key
```

Examples for other providers:

```dotenv
# OpenAI
ZHARNESS_MODEL=gpt-4o
OPENAI_API_KEY=your-api-key

# Anthropic
ZHARNESS_MODEL=claude-sonnet-4-5
ANTHROPIC_API_KEY=your-api-key
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

## Skills

The agent discovers reusable `SKILL.md` packages from a skills directory.
`LocalSkillStorage` resolves it in this order:

1. `ZHARNESS_SKILLS_PATH`, when set;
2. `<ZHARNESS_HOME>/skills`, when it exists;
3. the checked-in repository `skills/` directory;
4. `<ZHARNESS_HOME>/skills` otherwise.

Skills live in category subdirectories, `public/` (read-only, bundled) and
`user/` (editable):

```text
<root>/public/<name>/SKILL.md
<root>/user/<name>/SKILL.md
```

The system prompt embeds only a name-only `<skill_index>`. The model calls
`describe_skill` to fetch metadata (description, allowed tools, location) for
matching names, then reads the full `SKILL.md` with `read_file` if it wants to
load the skill. The catalog is rebuilt on each `describe_skill` call, so newly
installed or edited skills are reflected immediately.

The skills directory is mounted into every sandbox at the read-only
`/mnt/skills` path (both Docker and local providers). The agent can read skill
files there but cannot write, edit, or delete anything under it.

## Docker Sandbox

The Lead Agent creates or reuses one sandbox per LangGraph thread when the
first file or command tool runs. The thread workspace is mapped to the virtual
root `/`; the skills directory is mounted read-only at `/mnt/skills`. The
sandbox is constrained as follows:

- read-only root filesystem;
- network access through Docker's default bridge (disable with
  `ZHARNESS_SANDBOX_NETWORK=0`);
- all Linux capabilities dropped;
- `no-new-privileges` enabled;
- a `tmpfs` at `/tmp` with `nosuid`, `nodev`, and `noexec`;
- default limits of 1 CPU, 512 MiB of memory, and 128 processes.

Build the image from the repository root:

```bash
docker build -f docker/sandbox.Dockerfile -t zharness-sandbox:latest .
```

The sandbox image includes Python, uv, Git, GNU Coreutils, Findutils, Grep,
Ripgrep, curl, wget, and C build tools. The container has network access through
Docker's default bridge,
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
| `ZHARNESS_SANDBOX_NETWORK` | Enabled | Docker sandbox network access; set to `0`/`false`/`no` to disable |
| `ZHARNESS_SANDBOX_USER` | Server process UID/GID | Container user, for example `1000:1000` |
| `ZHARNESS_HOME` | `./.zharness` | Parent directory for thread workspaces |
| `ZHARNESS_LOCAL_ROOT` | None | Local sandbox root shared by every thread (otherwise each thread gets `ZHARNESS_HOME/workspaces/<thread_id>`) |
| `ZHARNESS_ALLOW_HOST_BASH` | Disabled | Enable host bash execution in the local sandbox (`1`/`true`/`yes`) |
| `ZHARNESS_SKILLS_PATH` | Resolved skills root | Override the directory that contains installed `SKILL.md` packages |

Commands are limited to 128 KiB of text and a timeout between 1 and 300 seconds.
Retained output is limited to 1 MiB by default. Docker sandbox file uploads and
downloads have a default per-file limit of 16 MiB; local file operations default
to a 256 KiB per-file limit.

The server process requires access to Docker Engine. Rootless Docker is
recommended, and the Docker socket must not be mounted inside sandbox
containers.

## Lifecycle

- Docker container names are derived from SHA-256 hashes of thread IDs; local
  sandboxes are tracked in memory and keyed by thread ID.
- Before reuse, a container's thread label, workspace mount, and security
  options are validated.
- After a LangGraph thread is successfully deleted, the custom HTTP middleware
  removes its sandbox: the Docker container, or the per-thread local workspace.
- During graceful shutdown, the server stops all running Docker containers
  labeled `zharness.sandbox=true` but retains them for reuse after a restart;
  running local host commands are terminated as well.
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

The test suite covers agent tool registration, middleware (including
human-in-the-loop approval), workspace path isolation, filesystem operations,
Docker and local sandbox behavior, command execution, skill parsing and
discovery, PostgreSQL checkpoints, and HTTP lifecycle cleanup.
