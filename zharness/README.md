# ZHarness

## Docker sandbox

The lead agent executes shell commands in one Docker container per LangGraph
thread. The server-owned thread workspace is mounted read-write at `/workspace`;
the container otherwise has a read-only root filesystem, no network, no Linux
capabilities, and bounded CPU, memory, and process counts.

Build the sandbox image before starting the server:

```bash
docker build -f docker/sandbox.Dockerfile -t zharness-sandbox:latest .
```

The server process needs access to Docker Engine. Prefer rootless Docker and do
not mount the Docker socket inside sandbox containers.

Configuration:

- `ZHARNESS_SANDBOX_IMAGE`: image name, default `zharness-sandbox:latest`.
- `ZHARNESS_SANDBOX_MEMORY`: memory limit, default `512m`.
- `ZHARNESS_SANDBOX_USER`: optional container UID/GID such as `1000:1000`;
  defaults to the server process UID/GID on POSIX hosts.
- `ZHARNESS_HOME`: host directory containing per-thread workspaces.

Containers are named from a hash of the server-validated thread ID and carry
labels binding them to that thread. Existing containers are reused only when
their labels and `/workspace` mount match the expected thread workspace.
Call `DockerSandboxManager.remove_for_thread(thread_id)` when deleting a thread;
an external TTL janitor can use the same method for abandoned threads.

During a graceful server shutdown (including a single `Ctrl+C`), the custom
Starlette lifespan stops every running container labelled
`zharness.sandbox=true`. Containers are retained and are started automatically
if their thread executes again after the server restarts. Forced termination
such as `kill -9` cannot run lifespan cleanup.
