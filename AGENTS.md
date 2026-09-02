# Project Guidelines

## Project Scope and Priorities

- This project is intended to build a personal AI Agent assistant for a single user.
- Do not design for or prioritize multi-instance deployment or authentication unless the project scope changes explicitly.
- Treat `zharness` as the current development priority and complete its core Agent runtime capabilities first.
- Defer work on `gateway` and frontend applications until `zharness` is sufficiently complete and stable, unless the user explicitly requests such work.

## Python Environment and Dependencies

- Use `uv` for Python dependency and environment management.

## Comments

- All code comments and docstrings must be bilingual, with English first and Chinese second.
- For single-line comments and docstrings, separate the English and Chinese text with ` / `.
- Single-line example: `"""Create TodoMiddleware only for plan mode. / 仅在计划模式下创建 TodoMiddleware。"""`
- For multiline comments and docstrings, place the English text above the Chinese text on separate lines; do not use `/` between them.
- Multiline example:

  ```python
  """Create TodoMiddleware only for plan mode.

  仅在计划模式下创建 TodoMiddleware。
  """
  ```
