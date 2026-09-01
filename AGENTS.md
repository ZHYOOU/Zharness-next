# Project Guidelines / 项目规范

## Comments / 注释

- All code comments and docstrings must be bilingual, with English first and Chinese second. / 所有代码注释和文档字符串必须使用中英双语，英文在前、中文在后。
- For single-line comments and docstrings, separate the English and Chinese text with ` / `. / 对于单行注释和文档字符串，英文与中文之间使用 ` / ` 分隔。
- Single-line example / 单行示例：`"""Create TodoMiddleware only for plan mode. / 仅在计划模式下创建 TodoMiddleware。"""`
- For multiline comments and docstrings, place the English text above the Chinese text on separate lines; do not use `/` between them. / 对于多行注释和文档字符串，英文内容在上、中文内容在下，使用换行分隔，不要在两者之间使用 `/`。
- Multiline example / 多行示例：

  ```python
  """Create TodoMiddleware only for plan mode.

  仅在计划模式下创建 TodoMiddleware。
  """
  ```
