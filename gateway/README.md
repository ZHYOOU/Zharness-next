# Gateway

English | [简体中文](README.zh-CN.md)

`gateway` is a Python package reserved for the future external access layer of
ZHarness Next.

## Current Status

The package does not yet implement gateway functionality. It currently exposes
only a minimal command-line entry point:

```bash
uv run gateway
```

The command prints:

```text
Hello from gateway!
```

There is currently no HTTP server, authentication, request forwarding, rate
limiting, session management, or LangGraph client wrapper. The actual Agent API
is provided by the LangGraph Server configured in the repository-level
`langgraph.json`.

## Package Layout

```text
gateway/
├── src/gateway/
│   └── __init__.py    # Currently contains only the CLI main function
├── pyproject.toml     # Package metadata and gateway command entry point
└── README.md
```

## Development

Install the complete workspace from the repository root:

```bash
uv sync --all-packages
```

Run the current entry point:

```bash
uv run gateway
```

If a gateway is implemented later, this package should own client-facing
protocol and platform concerns such as authentication, quotas, request
validation, and wrapping the LangGraph API. Agent execution, workspaces, and
Docker sandbox logic should remain in `zharness` to keep the layers decoupled.
