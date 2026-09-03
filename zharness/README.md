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
├── config/
│   ├── loader.py            # YAML loading with environment overrides
│   └── settings.py          # Typed configuration dataclasses
├── host/
│   └── paths.py             # Data home and thread workspace resolution
├── models/
│   └── factory.py           # Chat model factory
├── sandbox/
│   ├── base.py              # Shared sandbox backend implementation
│   ├── docker.py            # Docker sandbox implementation
│   ├── local.py             # Local filesystem sandbox implementation
│   ├── manager.py           # Thread-to-sandbox lifecycle mapping
│   ├── protocol.py          # Execution and file-transfer result types
│   └── workspace.py         # Shared /workspace path contract and validation
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
| `execute_command` | Run a shell command from a virtual workspace `cwd` |
| `describe_skill` | Fetch metadata for installed skills (registered when skills exist) |

The agent also enables:

- `TodoListMiddleware` for tracking multi-step tasks.
- `SummarizationMiddleware`, which uses model-specific context parameters. For
  `mimo-v2.5`, it summarizes at 786,432 tokens and retains the 32 most recent
  messages.
- `HumanInTheLoopMiddleware`, which supports per-run `allow_all` and
  `require_approval` strategies for `execute_command`; `allow_all` is the
  default.
- `ToolErrorMiddleware`, which formats tool failures for the model to fix and
  retry.
- `ToolRetryMiddleware`, which retries failed tool calls up to three times with
  bounded backoff.

## Configuration

### Approval strategy

Shell approval is selected per run through `configurable.approval_strategy`.
Omitting the value defaults to `allow_all`:

```python
config = {"configurable": {"approval_strategy": "allow_all"}}
```

Use `require_approval` to interrupt before each `execute_command` call and wait
for an explicit approve/reject decision:

```python
config = {"configurable": {"approval_strategy": "require_approval"}}
```

Clients should include the selected value on every run for the thread. The
strategy only controls human approval; sandbox, path, timeout, and output limits
remain enforced in both modes.

Non-secret settings live in `config.yaml` next to this package; secrets stay in
the `.env` file that `langgraph.json` loads. `zharness.config.loader` resolves
every value with the precedence: environment variable → YAML → built-in
default. This keeps temporary overrides (such as `ZHARNESS_HOME`) working as
environment variables while `config.yaml` remains the primary configuration
surface.

The lead agent and declarative subagents receive a hidden current-date reminder
at the beginning of their message history. Set the IANA `timezone` value in
`config.yaml`, or override it with `ZHARNESS_TIMEZONE`; the default is
`Asia/Shanghai`. The reminder is reused during the same local day and replaced
in place after midnight, so stale dates do not accumulate in the conversation.

## Model Configuration

`server/graph.py` reads the model name from `model.name` in `config.yaml` (or
`ZHARNESS_MODEL`). The model factory (`zharness.models.factory`) creates a chat
model for the provider selected by `model.provider` (or
`ZHARNESS_MODEL_PROVIDER`) — `mimo`, `deepseek`, `openai`, or `anthropic` — with a
temperature of `0`, a 60-second request timeout, and up to three retries. When
the provider is unset it is inferred from the model name: `mimo*` uses MiMo,
`claude*` uses Anthropic, `deepseek*` uses DeepSeek, and anything else uses
OpenAI. API keys are read from `MIMO_API_KEY`, `DEEPSEEK_API_KEY`,
`OPENAI_API_KEY`, and `ANTHROPIC_API_KEY` respectively. MiMo uses its
OpenAI-compatible endpoint, configurable through `model.mimo_base_url` or
`ZHARNESS_MIMO_BASE_URL`.

Minimum configuration:

```yaml
# zharness/config.yaml
model:
  name: mimo-v2.5
```

```dotenv
# zharness/.env (secrets only)
MIMO_API_KEY=your-api-key
```

Examples for other providers:

```yaml
# zharness/config.yaml
model:
  name: gpt-4o
  provider: openai

# or / 或
model:
  name: claude-sonnet-4-5
  provider: anthropic
```

## Thread Workspaces

Each LangGraph thread maps to a server-owned directory:

```text
<home>/workspaces/<thread_id>/
```

`<home>` is the `home` key in `config.yaml` (or `ZHARNESS_HOME`); when unset the
default is `.zharness` under the current working directory. Thread IDs may
contain letters, digits, underscores, and hyphens, with a maximum length of 128
characters.

`/workspace` is the stable path exposed to the agent, file tools, and command
execution. For example, `/workspace/src/main.py` names the same file in local
and Docker sandboxes; only the host storage path is translated internally.
All workspace inputs must be absolute paths under `/workspace`, and tool output
uses the same canonical namespace. The adapter rejects:

- `..` path traversal and `~` expansion;
- relative paths and absolute paths outside `/workspace` or `/mnt/skills`;
- attempts to operate on `/workspace` where a file path is required;
- binary reads through the UTF-8-only Agent tool contract.

Glob and Grep return at most 100 results by default. Writes use a temporary file
followed by `os.replace` to avoid leaving a partially written destination.

## Skills

The agent discovers reusable `SKILL.md` packages from a skills directory.
`LocalSkillStorage` resolves it in this order:

1. `ZHARNESS_SKILLS_PATH` or the `skills.path` YAML key, when set;
2. `<home>/skills`, when it exists;
3. the checked-in repository `skills/` directory;
4. `<home>/skills` otherwise.

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
  `sandbox.docker.network_enabled: false` or `ZHARNESS_SANDBOX_NETWORK=0`);
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
`sandbox.provider: local` in `config.yaml` (or `ZHARNESS_SANDBOX_PROVIDER=local`).
Each thread then reads and writes a local directory through the same virtual `/`
path space and path-safety checks as the Docker backend.

Sandbox configuration keys:

| Key | Default | Description |
| --- | --- | --- |
| `sandbox.provider` | `docker` | Sandbox backend: `docker` or `local` |
| `sandbox.docker.image` | `zharness-sandbox:latest` | Docker image name |
| `sandbox.docker.memory_limit` | `512m` | Container memory limit |
| `sandbox.docker.network_enabled` | `true` | Docker sandbox network access |
| `sandbox.docker.user` | Server process UID/GID | Container user, for example `1000:1000` |
| `sandbox.docker.idle_ttl_seconds` | `86400` | Remove containers idle for this many seconds; `0` disables TTL cleanup |
| `sandbox.docker.max_containers` | `5` | Maximum retained sandbox containers; `0` disables the count limit |
| `sandbox.docker.cleanup_interval_seconds` | `300` | Background cleanup interval in seconds |
| `home` | `./.zharness` | Parent directory for thread workspaces |
| `sandbox.local.root` | None | Local sandbox root shared by every thread (otherwise each thread gets `<home>/workspaces/<thread_id>`) |
| `sandbox.local.allow_host_bash` | `false` | Enable host bash execution in the local sandbox |
| `skills.path` | Resolved skills root | Override the directory that contains installed `SKILL.md` packages |

Commands are limited to 128 KiB of text and a timeout between 1 and 300 seconds.
The optional `cwd` uses workspace-tool paths (`/workspace` by default); the
tool validates and canonicalizes it before passing it to the active backend.
Shell directory changes are allowed and command strings are not rewritten.
Every external network request created by the agent must set an explicit
15-second request timeout.
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
  removes its Docker container and workspace, or its managed local workspace.
- A background task removes idle Docker containers and enforces the configured
  container limit. Active operations are never pruned, and workspaces remain
  available so a later thread operation can recreate its container.
- During graceful shutdown, the server removes all Docker sandbox containers;
  running local host commands are terminated as well. Thread workspaces remain
  available unless the corresponding thread was deleted.
- Forced termination such as `kill -9` cannot run shutdown cleanup. The next
  server startup performs an immediate cleanup pass before the periodic loop.

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
