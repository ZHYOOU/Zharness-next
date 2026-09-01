# Project Guidelines

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
