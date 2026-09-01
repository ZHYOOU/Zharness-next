"""Protocol definition for pluggable memory backends.

This module defines the `BackendProtocol` that all backend implementations
must follow. Backends can store files in different locations (state, filesystem,
database, etc.) and provide a uniform interface for file operations.

可插拔内存后端的协议定义。本模块定义了所有后端实现都必须遵循的
`BackendProtocol`。后端可以将文件存储在不同的位置（状态、文件系统、数据库等），
并为文件操作提供统一的接口。
"""

import abc
import asyncio
import inspect
import logging
from dataclasses import dataclass
from functools import lru_cache, partial
from typing import Final, Literal, NotRequired

from typing_extensions import TypedDict

logger = logging.getLogger(__name__)

DEFAULT_GREP_TIMEOUT: Final = 15
"""Default timeout in seconds for one sync grep phase. / 单次同步 grep 阶段的默认超时时间（秒）。"""

ASYNC_GREP_TIMEOUT: Final = (2 * DEFAULT_GREP_TIMEOUT) + 5
"""Timeout in seconds for the async grep wrapper.

This gives `LocalSandbox` enough headroom to finish the worst-case sync
path: ripgrep timeout, then Python fallback timeout.

异步 grep 包装器的超时时间（秒）。这为 `LocalSandbox` 留出足够余量来完成最坏
情况下的同步路径：先是 ripgrep 超时，然后是 Python 回退超时。
"""

ASYNC_GLOB_TIMEOUT: Final = 30
"""Timeout in seconds for a sandbox glob round-trip.

The remote script bounds its own walk (`TIME_BUDGET` in `sandbox.py`), but that
covers neither interpreter startup, the sandbox round-trip, nor transferring up
to `MAX_MATCHES` records. Without an outer bound a wedged sandbox hangs the
caller indefinitely.

沙箱 glob 往返的超时时间（秒）。远程脚本限制了自身的遍历（`sandbox.py` 中的
`TIME_BUDGET`），但该限制既不覆盖解释器启动、沙箱往返，也不覆盖最多传输
`MAX_MATCHES` 条记录。若没有外层限制，卡死的沙箱会让调用方无限期挂起。
"""

FileOperationError = Literal[
    "file_not_found",
    "permission_denied",
    "is_directory",
    "invalid_path",
]
"""Standardized error codes for file upload/download operations.

These represent common, recoverable errors that an LLM can understand and
potentially fix:

- `file_not_found`: The requested file doesn't exist (download)
- `permission_denied`: Access denied for the operation
- `is_directory`: Attempted to download a directory as a file
- `invalid_path`: Path syntax is malformed or contains invalid characters

文件上传/下载操作的标准化错误码。这些表示 LLM 可以理解并可能修复的常见、
可恢复的错误。具体含义如下：

- `file_not_found`：请求的文件不存在（下载）
- `permission_denied`：操作被拒绝访问
- `is_directory`：尝试将目录作为文件下载
- `invalid_path`：路径语法格式错误或包含无效字符
"""

# Named constants for each `FileOperationError` literal. Use these instead of
# bare string literals at producer/consumer sites so a rename in one place
# surfaces as a type error (rather than silently reverting to a fallback branch).
# 每个 `FileOperationError` 字面量的命名常量。请在生产者/消费者处使用它们而不是
# 裸字符串字面量，这样在某个位置重命名会表现为类型错误（而非静默回退到回退分支）。
FILE_NOT_FOUND: Final = "file_not_found"
PERMISSION_DENIED: Final = "permission_denied"
IS_DIRECTORY: Final = "is_directory"
INVALID_PATH: Final = "invalid_path"


@dataclass
class FileDownloadResponse:
    """Result of a single file download operation.

    The response is designed to allow partial success in batch operations.

    The errors are standardized using `FileOperationError` literals for certain
    recoverable conditions for use cases that involve LLMs performing
    file operations.

    Examples:
        >>> # Success
        >>> FileDownloadResponse(path="/app/config.json", content=b"{...}", error=None)
        >>> # Failure
        >>> FileDownloadResponse(path="/wrong/path.txt", content=None, error="file_not_found")

    单次文件下载操作的结果。该响应设计为允许批量操作中的部分成功。对于涉及
    LLM 执行文件操作的场景，某些可恢复的条件会使用 `FileOperationError` 字面量
    进行标准化。
    """

    path: str
    """The file path that was requested. Included for easy correlation when
    processing batch results, especially useful for error messages.

    所请求的文件路径。用于在处理批量结果时便于关联，尤其是在错误信息中很有用。
    """

    content: bytes | None = None
    """File contents as bytes on success, `None` on failure. / 成功时以字节形式返回文件内容，失败时为 `None`。"""

    error: FileOperationError | str | None = None
    """A `FileOperationError` literal for known conditions, or a
    backend-specific error string when the failure cannot be normalized.

    `None` on success.

    已知条件的 `FileOperationError` 字面量，或当失败无法规范化时返回的后端
    特定错误字符串。成功时为 `None`。
    """


@dataclass
class FileUploadResponse:
    """Result of a single file upload operation.

    The response is designed to allow partial success in batch operations.

    The errors are standardized using `FileOperationError` literals for certain
    recoverable conditions for use cases that involve LLMs performing
    file operations.

    Examples:
        >>> # Success
        >>> FileUploadResponse(path="/app/data.txt", error=None)
        >>> # Failure
        >>> FileUploadResponse(path="/readonly/file.txt", error="permission_denied")

    单次文件上传操作的结果。该响应设计为允许批量操作中的部分成功。对于涉及
    LLM 执行文件操作的场景，某些可恢复的条件会使用 `FileOperationError` 字面量
    进行标准化。
    """

    path: str
    """The file path that was requested.

    Included for easy correlation when processing batch results and for clear
    error messages.

    所请求的文件路径。用于在处理批量结果时便于关联，并用于生成清晰的错误信息。
    """

    error: FileOperationError | str | None = None
    """A `FileOperationError` literal for known conditions, or a
    backend-specific error string when the failure cannot be normalized.

    `None` on success.

    已知条件的 `FileOperationError` 字面量，或当失败无法规范化时返回的后端
    特定错误字符串。成功时为 `None`。
    """


class FileInfo(TypedDict):
    """Structured file listing info.

    Minimal contract used across backends. Only `path` is required.
    Other fields are best-effort and may be absent depending on backend.

    结构化的文件列表信息。各后端共用的最小契约，只有 `path` 是必需的。
    其他字段是尽力而为的，可能因后端而异而缺失。
    """

    path: str
    """Absolute or relative file path. / 绝对或相对的文件路径。"""

    is_dir: NotRequired[bool]
    """Whether the entry is a directory. / 该条目是否为目录。"""

    size: NotRequired[int]
    """File size in bytes (approximate). / 文件大小（字节，近似值）。"""

    modified_at: NotRequired[str]
    """ISO 8601 timestamp of last modification, if known. / 已知情况下最后一次修改的 ISO 8601 时间戳。"""


class ContextLine(TypedDict):
    """A non-matching line surrounding a grep match, used for `context_lines`. / grep 匹配周围的不匹配行，用于 `context_lines`。"""

    line: int
    """1-indexed line number of the context line. / 上下文行的行号（从 1 开始计数）。"""

    text: str
    """Content of the context line. / 上下文行的内容。"""


class GrepMatch(TypedDict):
    """A single match from a grep search. / grep 搜索的单个匹配结果。"""

    path: str
    """Path to the file containing the match. / 包含匹配的文件路径。"""

    line: int
    """1-indexed line number of the match. / 匹配行的行号（从 1 开始计数）。"""

    text: str
    """Content of the matching line. / 匹配行的内容。"""

    context_before: NotRequired[list[ContextLine]]
    """Context lines before the match.

    Present (alongside `context_after`) only when a backend was asked for
    `context_lines > 0` (via a backend-specific argument, e.g.
    `LocalSandbox.grep`); both keys are set together on every match or on
    none. An empty list means no context lines were available on that side: the
    match sits at the file boundary, the adjacent line was itself a match
    (matches are never repeated as context), or the file could not be re-read
    (in which case the failure is reported in `GrepResult.error`).

    匹配之前的上下文行。仅当请求后端提供 `context_lines > 0` 时才会出现
    （与 `context_after` 一起，通过后端特定的参数，例如 `LocalSandbox.grep`）；
    两个键在每条匹配上要么同时设置，要么都不设置。空列表表示该侧没有可用的
    上下文行：匹配位于文件边界、相邻行本身也是匹配（匹配不会重复作为上下文），
    或者文件无法重新读取（此时失败信息会记录在 `GrepResult.error` 中）。
    """

    context_after: NotRequired[list[ContextLine]]
    """Context lines after the match. See `context_before` for presence rules. / 匹配之后的上下文行。出现规则参见 `context_before`。"""


class FileData(TypedDict):
    """Data structure for storing file contents with metadata. / 用于存储带元数据的文件内容的数据结构。"""

    content: str
    """File content as a plain string (utf-8 text or base64-encoded binary). / 以普通字符串形式表示的文件内容（utf-8 文本或 base64 编码的二进制数据）。"""

    encoding: str
    """Content encoding: `"utf-8"` for text, `"base64"` for binary. / 内容编码：文本用 `"utf-8"`，二进制用 `"base64"`。"""

    created_at: NotRequired[str]
    """ISO 8601 timestamp of file creation. / 文件创建时间的 ISO 8601 时间戳。"""

    modified_at: NotRequired[str]
    """ISO 8601 timestamp of last modification. / 最后修改时间的 ISO 8601 时间戳。"""


@dataclass
class ReadResult:
    """Result from backend read operations. / 后端读取操作的结果。"""

    error: str | None = None
    """Error message on failure, `None` on success. / 失败时的错误信息，成功时为 `None`。"""

    file_data: FileData | None = None
    """File data on success, `None` on failure. / 成功时的文件数据，失败时为 `None`。"""

    total_lines: int | None = None
    """Total number of source lines when the backend can determine it. / 当后端能够确定时，源文件的总行数。"""

    start_line: int | None = None
    """1-indexed first source line returned in `file_data`. / `file_data` 中返回的第一行源文件行号（从 1 开始计数）。"""

    end_line: int | None = None
    """1-indexed last source line returned in `file_data`. / `file_data` 中返回的最后一行源文件行号（从 1 开始计数）。"""

    next_offset: int | None = None
    """0-indexed offset for the next unread source line. / 下一未读源文件行的偏移量（从 0 开始计数）。"""

    no_lines_requested: bool = False
    """The read asked for zero lines and the file was never inspected.

    Set by backends when a non-positive `limit` short-circuits the read, so
    the middleware can tell a never-inspected window apart from a file that
    was inspected and is genuinely empty — both otherwise arrive as empty
    content with no pagination metadata.

    读取请求了零行，文件从未被检查。当非正数的 `limit` 使读取短路时由后端设置，
    这样中间件就能区分从未被检查的窗口与已被检查但确实为空的文件——否则两者
    都会以空内容和无分页元数据的形式到达。
    """

    def __post_init__(self) -> None:
        """Reject malformed pagination-field combinations at construction.

        The window fields are not independent: `start_line`/`end_line` are a
        pair, and neither `next_offset` nor `total_lines` describes anything
        without the window it refers to. Beyond co-presence, the values must
        agree numerically: a window runs forward (`1 <= start_line <=
        end_line`), the file is at least as long as the window
        (`total_lines >= end_line`), and the resume point is the 0-indexed line
        immediately after the last one shown (`next_offset == end_line`, since
        `end_line` is 1-indexed). Fail loudly here to keep a backend from
        emitting a `next_offset` that would silently skip unshown source lines
        once it reaches the middleware.

        在构造时拒绝格式错误的分页字段组合。窗口字段并非相互独立：
        `start_line`/`end_line` 是一对，且 `next_offset` 和 `total_lines` 若没有
        其所指的窗口则没有任何意义。除需同时出现外，数值也必须一致：窗口向前推进
        （`1 <= start_line <= end_line`），文件至少与窗口等长（`total_lines >=
        end_line`），续读点是最后一个已显示行的后一行（0 起始，即 `next_offset ==
        end_line`，因为 `end_line` 是 1 起始）。在此处大声失败，可避免后端发出
        一旦到达中间件就会静默跳过未显示源文件行的 `next_offset`。
        """
        if (self.start_line is None) != (self.end_line is None):
            msg = "ReadResult.start_line and end_line must be set together or both left unset"
            raise ValueError(msg)
        if self.no_lines_requested and (
            self.error is not None
            or self.start_line is not None
            or self.next_offset is not None
            or self.total_lines is not None
        ):
            msg = "ReadResult.no_lines_requested describes an uninspected window; it cannot be combined with error or pagination fields"
            raise ValueError(msg)
        if self.next_offset is not None and self.start_line is None:
            msg = "ReadResult.next_offset requires start_line and end_line to be set"
            raise ValueError(msg)
        if self.total_lines is not None and self.start_line is None:
            msg = "ReadResult.total_lines requires start_line and end_line to be set"
            raise ValueError(msg)

        # Numeric consistency of a present window. `start_line`/`end_line` are
        # bound together above, so testing `start_line` covers both.
        # 已出现窗口的数值一致性。`start_line`/`end_line` 在上面已绑定在一起，
        # 因此测试 `start_line` 即可覆盖两者。
        if self.start_line is not None and self.end_line is not None:
            if self.start_line < 1 or self.end_line < self.start_line:
                msg = f"ReadResult window must satisfy 1 <= start_line <= end_line, got start_line={self.start_line}, end_line={self.end_line}"
                raise ValueError(msg)
            if self.total_lines is not None and self.total_lines < self.end_line:
                msg = f"ReadResult.total_lines ({self.total_lines}) cannot be less than end_line ({self.end_line})"
                raise ValueError(msg)
            if self.next_offset is not None and self.next_offset != self.end_line:
                msg = f"ReadResult.next_offset ({self.next_offset}) must equal end_line ({self.end_line}), the 0-indexed line after the last shown"
                raise ValueError(msg)


@dataclass
class WriteResult:
    """Result from backend `write` operations.

    Attributes:
        error: Error message on failure, `None` on success.
        path: Absolute path of written file, `None` on failure.

    Examples:
        >>> WriteResult(path="/f.txt")
        >>> WriteResult(error="File exists")

    后端 `write` 操作的结果。Attributes：`error` 为失败时的错误信息，成功时为
    `None`；`path` 为写入文件的绝对路径，失败时为 `None`。
    """

    error: str | None = None
    path: str | None = None


@dataclass
class EditResult:
    """Result from backend `edit` operations.

    Attributes:
        error: Error message on failure, `None` on success.
        path: Absolute path of edited file, `None` on failure.
        occurrences: Number of replacements made, `None` on failure.

    Examples:
        >>> EditResult(path="/f.txt", occurrences=1)
        >>> EditResult(error="File not found")

    后端 `edit` 操作的结果。Attributes：`error` 为失败时的错误信息，成功时为
    `None`；`path` 为编辑后文件的绝对路径，失败时为 `None`；`occurrences` 为
    执行的替换次数，失败时为 `None`。
    """

    error: str | None = None
    path: str | None = None
    occurrences: int | None = None


@dataclass
class DeleteResult:
    """Result from backend delete operations.

    Attributes:
        error: Error message on failure, None on success.
        path: Absolute path of the deleted file, None on failure.

    Examples:
        >>> DeleteResult(path="/f.txt")
        >>> DeleteResult(error="File not found")

    后端删除操作的结果。Attributes：`error` 为失败时的错误信息，成功时为
    `None`；`path` 为被删除文件的绝对路径，失败时为 `None`。
    """

    error: str | None = None
    path: str | None = None


@dataclass
class LsResult:
    """Result from backend `ls` operations.

    Attributes:
        error: Error message on failure, `None` on success.
        entries: List of file info dicts on success, `None` on failure.

    后端 `ls` 操作的结果。Attributes：`error` 为失败时的错误信息，成功时为
    `None`；`entries` 为成功时的文件信息字典列表，失败时为 `None`。
    """

    error: str | None = None
    entries: list[FileInfo] | None = None


@dataclass
class GrepResult:
    """Result from backend `grep` operations.

    Attributes:
        error: Error message on failure, `None` on success.
        matches: List of grep match dicts. Populated on success and, when the
            search was cut short, with whatever was found before stopping.
            `None` only on a hard failure.
        truncated: True when the search stopped early (e.g. hit its time limit)
            and `matches` is therefore incomplete but still valid.

    后端 `grep` 操作的结果。Attributes：`error` 为失败时的错误信息，成功时为
    `None`；`matches` 为 grep 匹配字典列表，成功时填充，且当搜索被提前截断时
    包含停止前已找到的内容，仅在严重失败时为 `None`；`truncated` 在搜索提前
    停止（例如达到时间限制）时为 `True`，此时 `matches` 不完整但仍然有效。
    """

    error: str | None = None
    matches: list[GrepMatch] | None = None
    truncated: bool = False


def _apply_grep_max_count(result: GrepResult, max_count: int | None) -> GrepResult:
    """Enforce a match cap after a backend search has completed. / 在后端搜索完成后强制限制匹配数量上限。"""
    if max_count is None or result.matches is None or len(result.matches) <= max_count:
        return result
    return GrepResult(
        error=result.error, matches=result.matches[:max_count], truncated=True
    )


GlobTruncationReason = Literal["budget", "unreadable", "transport"]
"""Why a `GlobResult` is incomplete.

The distinction decides what advice is useful to the caller:

- `budget`: the walk hit its time limit or match cap. Narrowing the pattern or
  the path surfaces the rest.
- `unreadable`: a subtree could not be read (e.g. permissions). Narrowing will
  *never* surface those files, so advising it sends the caller in a loop.
- `transport`: the sandbox transport clipped the output.

`GlobResult` 不完整的原因。该区分决定了哪些建议对调用者有用：

- `budget`：遍历达到其时间限制或匹配数量上限。缩小模式或路径即可得到其余结果。
- `unreadable`：某个子树无法读取（例如权限问题）。缩小范围也*永远*无法得到
  那些文件，因此建议缩小会让调用者陷入循环。
- `transport`：沙箱传输层截断了输出。
"""


@dataclass
class GlobResult:
    """Result from backend `glob` operations.

    Attributes:
        error: Error message on failure, `None` on success.
        matches: List of matching file info dicts. Populated on success and,
            when the walk was cut short, with whatever was found before
            stopping. `None` only on a hard failure.
        truncated: True when the walk stopped early (e.g. hit its time limit)
            and `matches` is therefore incomplete but still valid.
        truncation_reason: Why `matches` is incomplete. Set whenever the
            producing backend can distinguish the cause; `None` when
            `truncated` is False or the cause is unknown.

    后端 `glob` 操作的结果。Attributes：`error` 为失败时的错误信息，成功时为
    `None`；`matches` 为匹配的文件信息字典列表，成功时填充，且当遍历被提前截断时
    包含停止前已找到的内容，仅在严重失败时为 `None`；`truncated` 在遍历提前
    停止（例如达到时间限制）时为 `True`，此时 `matches` 不完整但仍然有效；
    `truncation_reason` 说明 `matches` 不完整的原因，当产生结果的后端能区分
    原因时设置，当 `truncated` 为 `False` 或原因未知时为 `None`。
    """

    error: str | None = None
    matches: list[FileInfo] | None = None
    truncated: bool = False
    truncation_reason: GlobTruncationReason | None = None


# @abstractmethod to avoid breaking subclasses that only implement a subset of the protocol. / @abstractmethod 用于避免破坏那些只实现了协议中部分方法的子类。
class BackendProtocol(abc.ABC):
    r"""Protocol for pluggable memory backends (single, unified).

    Backends can store files in different locations (state, filesystem,
    database, etc.) and provide a uniform interface for file operations.

    File operations (`grep`, `glob`, `ls`, `read`, etc.) live on this base
    protocol rather than only on `SandboxBackendProtocol` because not every
    backend has a shell. Backends that store files in in-memory state or a
    remote store with no process to exec into implement `grep`/`glob` in pure
    Python and have no `execute` at all.
    Even on shell-capable backends, the tools are not just convenience
    wrappers around `execute`: they enforce literal-only matching (not
    regex), return structured `GrepResult`/`GlobResult` objects, support
    `max_count` truncation, and pass through filesystem permission rules —
    none of which raw `execute` + shell `grep`/`find` provides. Agent-facing
    prompt guidance should therefore recommend these tools only when they
    are actually registered, and never assume a shell is available as a
    fallback.

    可插拔内存后端（单一、统一）的协议。后端可以将文件存储在不同的位置（状态、
    文件系统、数据库等），并为文件操作提供统一的接口。

    文件操作（`grep`、`glob`、`ls`、`read` 等）位于这个基础协议上，而不仅仅在
    `SandboxBackendProtocol` 上，因为并非每个后端都有 shell。那些将文件存储在
    内存状态或无进程可执行的环境的远程存储中的后端，会在纯 Python 中实现
    `grep`/`glob`，并且完全没有 `execute`。即使在支持 shell 的后端上，这些工具
    也不仅仅是 `execute` 的便捷包装：它们强制仅进行字面量匹配（非正则），返回
    结构化的 `GrepResult`/`GlobResult` 对象，支持 `max_count` 截断，并透传文件
    系统权限规则——这些都是裸 `execute` + shell `grep`/`find` 所不具备的。因此，
    面向 Agent 的提示词指南应仅在这些工具实际注册时才推荐使用它们，并且绝不要
    假设 shell 可作为回退使用。所有文件数据均以如下结构的字典表示：

    All file data is represented as dicts with the following structure:

    ```python
    {
        "content": str,  # Text content (utf-8) or base64-encoded binary
        "encoding": str,  # "utf-8" for text, "base64" for binary data
        "created_at": str,  # ISO format timestamp
        "modified_at": str,  # ISO format timestamp
    }
    ```
    """

    def ls(self, path: str) -> LsResult:
        """List all files in a directory with metadata.

        列出目录中的所有文件及其元数据。

        Args:
            path: Absolute path to the directory to list. Must start with `'/'`.

                要列出的目录的绝对路径，必须以 `'/'` 开头。

        Returns:
            `LsResult` with directory entries or error.

            返回包含目录条目或错误的 `LsResult`。

        Raises:
            NotImplementedError: If the backend does not implement `ls`.

            如果后端未实现 `ls`，则抛出 `NotImplementedError`。
        """
        raise NotImplementedError

    async def als(self, path: str) -> LsResult:
        """Async version of `ls`. / `ls` 的异步版本。"""
        return await asyncio.to_thread(self.ls, path)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Read file content for the requested line range.

        读取请求行范围内的文件内容。

        Implementations must tolerate degenerate windows rather than raising:
        a negative `offset` reads from the first line, and a non-positive
        `limit` returns empty content with every pagination field unset.
        `zharness.utils.normalize_read_bounds` clamps both bounds for
        implementations that slice in Python.

        实现必须容忍退化的窗口而不是抛异常：负的 `offset` 从第一行开始读取，
        非正数的 `limit` 返回空内容且所有分页字段均不设置。
        `zharness.utils.normalize_read_bounds` 为在 Python 中进行切片切分的
        实现钳制两个边界。

        Implementations must also set `start_line` whenever they return
        line-numberable text. The middleware falls back to deriving the gutter
        from `offset` when `start_line` is unset, which only yields a valid
        1-indexed gutter for windows the backend actually sliced.

        实现还必须在返回带行号的文本时设置 `start_line`。当 `start_line` 未设置时，
        中间件会回退到从 `offset` 推导行号列，这仅对后端实际切片过的窗口能产生
        有效的从 1 开始计数的行号列。

        Args:
            file_path: Absolute path to the file to read. Must start with `'/'`.

                要读取的文件的绝对路径，必须以 `'/'` 开头。

            offset: Line number to start reading from (0-indexed).

                开始读取的行号（从 0 开始计数）。

            limit: Maximum number of lines to read.

                要读取的最大行数。

        Returns:
            `ReadResult` with raw (unformatted) content for the requested window,
                or an error if the file doesn't exist or can't be read.

                返回包含请求窗口的原始（未格式化）内容的 `ReadResult`，或当文件
                不存在或无法读取时返回错误。

                Line-number formatting is applied downstream by the filesystem
                middleware (`format_content_with_line_numbers`), not by backends:
                it adds the gutter, starts numbering at `offset + 1`, and splits
                lines longer than 5000 characters into continuation rows
                (e.g., `5.1`, `5.2`).

                行号格式化由下游的文件系统中间件
                （`format_content_with_line_numbers`）完成，而不是后端：它会添加
                行号列、从 `offset + 1` 开始编号，并将超过 5000 个字符的行拆分为
                续行（例如 `5.1`、`5.2`）。
        """
        raise NotImplementedError

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Async version of read. / read 的异步版本。"""
        return await asyncio.to_thread(self.read, file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Search for a literal text pattern in files.

        在文件中搜索字面量文本模式。

        Args:
            pattern: Literal string to search for (NOT regex).

                要搜索的字面量字符串（不是正则表达式）。

                Performs exact substring matching within file content.

                在文件内容中执行精确的子串匹配。

                Example: `"TODO"` matches any line containing `"TODO"`

                例如：`"TODO"` 匹配任何包含 `"TODO"` 的行。

            path: Optional directory path to search in.

                可选的要搜索的目录路径。

                If `None`, searches in current working directory.

                如果为 `None`，则在当前工作目录中搜索。

                Example: `'/workspace/src'`

                例如：`'/workspace/src'`

            glob: Optional glob pattern to filter which FILES to search.

                可选的用于过滤要搜索哪些文件的 glob 模式。

                Filters by filename/path, not content.

                按文件名/路径过滤，而不是按内容过滤。

                Supports standard glob wildcards:

                支持标准 glob 通配符：

                - `*` matches any characters in filename
                - `**` matches any directories recursively
                - `?` matches single character
                - `[abc]` matches one character from set

                - `*` 匹配文件名中的任意字符
                - `**` 递归匹配任意目录
                - `?` 匹配单个字符
                - `[abc]` 匹配集合中的一个字符

            max_count: Optional total cap on the number of matches returned
                across all files.

                跨所有文件返回的匹配数量的可选总上限。

                `None` (the default) preserves existing backend behavior and
                returns every match. When set to an int, at most that many
                matches are returned; if more exist the search stops and the
                result is flagged with `GrepResult.truncated=True`. Exactly
                `max_count` matches with none dropped is reported complete
                (`truncated=False`). Interpreted as a total cap, not a per-file
                cap.

                `None`（默认值）保留现有后端行为并返回所有匹配。设置为 int 时，
                最多返回这么多匹配；如果存在更多，搜索会停止，并将结果标记为
                `GrepResult.truncated=True`。恰好 `max_count` 个匹配且无丢弃时
                报告为完整（`truncated=False`）。该值被解释为总上限，而非每个
                文件的上限。

        Examples:
            - `'*.py'` - only search Python files
            - `'**/*.txt'` - search all `.txt` files recursively
            - `'src/**/*.js'` - search JS files under src/
            - `'test[0-9].txt'` - search `test0.txt`, `test1.txt`, etc.

            例如：
            - `'*.py'` - 仅搜索 Python 文件
            - `'**/*.txt'` - 递归搜索所有 `.txt` 文件
            - `'src/**/*.js'` - 搜索 src/ 下的 JS 文件
            - `'test[0-9].txt'` - 搜索 `test0.txt`、`test1.txt` 等

        Returns:
            `GrepResult` with matches or error.

            返回包含匹配或错误的 `GrepResult`。

        Raises:
            NotImplementedError: If the backend does not implement `grep`.

            如果后端未实现 `grep`，则抛出 `NotImplementedError`。
        """
        raise NotImplementedError

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Async version of `grep`.

        `grep` 的异步版本。

        Wraps the sync call with an async timeout as a safety net. The timeout
        bounds how long the caller waits; it does not stop the worker thread
        created by `asyncio.to_thread`.

        将同步调用包装为带异步超时的安全网。超时限制调用方的等待时间；它不会
        停止由 `asyncio.to_thread` 创建的工作线程。

        `max_count` is forwarded when the concrete `grep` accepts it (so the
        search can bound itself); backends that don't accept it run uncapped and
        are trimmed afterward. Either way the return value is always passed
        through `_apply_grep_max_count` (a no-op when already within the cap), so
        callers get the same guarantee regardless of which path runs.

        当具体的 `grep` 接受 `max_count` 时会被转发（这样搜索可以自我限制）；
        不接受它的后端以无上限方式运行，随后再被裁剪。无论走哪条路径，返回值
        始终经过 `_apply_grep_max_count` 处理（已在限制内时为无操作），因此
        调用方总能获得相同的保证。
        """
        grep_kwargs = (
            {"max_count": max_count}
            if _method_accepts_max_count(type(self), "grep")
            else {}
        )
        grep_call = partial(self.grep, pattern, path, glob, **grep_kwargs)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(grep_call),
                timeout=ASYNC_GREP_TIMEOUT,
            )
            return _apply_grep_max_count(result, max_count)
        except TimeoutError:
            logger.warning(
                "agrep timed out after %ds (pattern=%r, path=%r, glob=%r)",
                ASYNC_GREP_TIMEOUT,
                pattern,
                path,
                glob,
            )
            return GrepResult(
                error=f"Error: grep timed out after {ASYNC_GREP_TIMEOUT}s. Try a more specific pattern or a narrower path.",
            )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Find files matching a glob pattern.

        查找匹配 glob 模式的文件。

        Pattern matching follows the shared backend contract (aligned with
        grep include-glob, not classic non-recursive shell globbing):

        模式匹配遵循共享的后端契约（与 grep 的 include-glob 一致，而不是
        经典的非递归 shell globbing）：

        - Patterns without `/` match the basename at any depth under `path`.

            不含 `/` 的模式在 `path` 下的任意深度匹配基名。

            Example: `*.py` matches `src/app/main.py`.

            例如：`*.py` 匹配 `src/app/main.py`。
        - Patterns containing `/` match paths relative to the search root, with
          `**` support.

            包含 `/` 的模式相对于搜索根目录匹配路径，并支持 `**`。

            Example: `src/**/*.py` matches `src/app/main.py`.

            例如：`src/**/*.py` 匹配 `src/app/main.py`。
        - A leading `/` anchors the pattern to the search root; it narrows the
          match rather than widening it.

            开头的 `/` 将模式锚定到搜索根目录；它会缩小匹配范围而不是扩大。

            Example: `/*.py` matches `top.py` but not `src/app/main.py`.

            例如：`/*.py` 匹配 `top.py` 但不匹配 `src/app/main.py`。
        - Leading-dot names match only when the pattern segment itself starts
          with `.`. Since `**` will not descend into dot-directories, a bare
          pattern is *broader* than its `**/` form.

            前导点名称仅在模式段本身以 `.` 开头时才会匹配。由于 `**` 不会进入
            以点开头的目录，因此裸模式比其 `**/` 形式*更宽泛*。

            Example: `*.yml` matches `.github/workflows/ci.yml`; `**/*.yml`
            does not. `.env` matches `.env`; `*` does not.

            例如：`*.yml` 匹配 `.github/workflows/ci.yml`；`**/*.yml` 则不匹配。
            `.env` 匹配 `.env`；`*` 不匹配。

        Only regular files are returned; directories are never matched.

        只返回常规文件；目录永远不会被匹配。

        Args:
            pattern: Glob pattern with wildcards to match file paths.

                带通配符、用于匹配文件路径的 glob 模式。

                Supports:

                支持：

                - `*` matches any characters within a path segment
                - `**` matches any directories recursively
                - `?` matches a single character
                - `[abc]` matches one character from a set, `[!abc]` negates
                - `{a,b}` brace expansion, including nested groups

                - `*` 匹配路径段内的任意字符
                - `**` 递归匹配任意目录
                - `?` 匹配单个字符
                - `[abc]` 匹配集合中的一个字符，`[!abc]` 取反
                - `{a,b}` 花括号展开，包括嵌套组

            path: Optional base directory to search from.

                可选的要从中搜索的基目录。

                If omitted, the backend chooses its default search root.

                如果省略，后端会选择其默认搜索根目录。

                The pattern is applied relative to this path.

                模式将相对于该路径应用。

        Returns:
            `GlobResult` with matching files or error. Patterns the matcher
            refuses -- brace expansion past its limit, or a `..` segment -- are
            reported as `error` with `matches=None`, not raised.

            返回包含匹配文件或错误的 `GlobResult`。匹配器拒绝的模式——超出限制
            的花括号展开，或包含 `..` 的段——会被报告为 `error` 且
            `matches=None`，而不是抛异常。

            `FileInfo.path` is always absolute. `_check_fs_permission` matches
            `deny` rules against absolute patterns only, so a backend returning
            a relative path silently bypasses every deny rule.

            `FileInfo.path` 始终是绝对路径。`_check_fs_permission` 仅针对绝对
            模式匹配 `deny` 规则，因此返回相对路径的后端会静默绕过所有拒绝规则。

        Raises:
            NotImplementedError: If the backend does not implement `glob`.

            如果后端未实现 `glob`，则抛出 `NotImplementedError`。
        """
        raise NotImplementedError

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Async version of `glob`. / `glob` 的异步版本。"""
        return await asyncio.to_thread(self.glob, pattern, path)

    def write(
        self,
        file_path: str,
        content: str,
    ) -> WriteResult:
        """Write content to a file, creating it or overwriting it if it already exists.

        将内容写入文件，若文件已存在则覆盖它。

        Args:
            file_path: Absolute path where the file should be written.

                要写入文件的绝对路径。

                Must start with '/'.

                必须以 '/' 开头。

            content: String content to write to the file.

                要写入文件的字符串内容。

        Returns:
            WriteResult

            返回 WriteResult。
        """
        raise NotImplementedError

    async def awrite(
        self,
        file_path: str,
        content: str,
    ) -> WriteResult:
        """Async version of write. / write 的异步版本。"""
        return await asyncio.to_thread(self.write, file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Perform exact string replacements in an existing file.

        在现有文件中执行精确的字符串替换。

        Args:
            file_path: Absolute path to the file to edit. Must start with `'/'`.

                要编辑的文件的绝对路径，必须以 `'/'` 开头。

            old_string: Exact string to search for and replace.

                要搜索并替换的精确字符串。

                Must match exactly including whitespace and indentation.

                必须完全匹配，包括空白和缩进。

            new_string: String to replace old_string with.

                用于替换 old_string 的字符串。

                Must be different from old_string.

                必须与 old_string 不同。

            replace_all: If True, replace all occurrences.

                如果为 True，则替换所有出现的位置。

                If `False` (default), `old_string` must be unique in the file or
                the edit fails.

                如果为 `False`（默认值），`old_string` 必须在文件中唯一，否则
                编辑会失败。

        Returns:
            EditResult

            返回 EditResult。
        """
        raise NotImplementedError

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Async version of edit. / edit 的异步版本。"""
        return await asyncio.to_thread(
            self.edit, file_path, old_string, new_string, replace_all
        )

    def delete(self, file_path: str) -> DeleteResult:
        """Delete a path, recursively removing anything nested under it.

        删除一个路径，并递归删除其下嵌套的所有内容。

        This method is optional. Backends that do not implement it inherit this
        default, which raises `NotImplementedError`. Callers that need to support
        a mix of backends should guard with
        [`_supports_delete`][zharness.sandbox.protocol._supports_delete] before
        calling, or catch `NotImplementedError`.

        此方法是可选的。未实现它的后端会继承此默认实现，该默认实现会抛出
        `NotImplementedError`。需要支持多种后端的调用方应在调用前使用
        [`_supports_delete`][zharness.sandbox.protocol._supports_delete] 进行
        判断，或捕获 `NotImplementedError`。

        Deletion is recursive: it removes `file_path` plus everything nested
        under it. On hierarchical backends (e.g.
        [`LocalSandbox`][zharness.sandbox.local.LocalSandbox])
        that means a directory and its contents; on key-value backends it means
        the exact key plus every key sharing the `file_path` + "/" prefix.

        删除是递归的：它会移除 `file_path` 及其下嵌套的所有内容。在分层后端上
        （例如 [`LocalSandbox`][zharness.sandbox.local.LocalSandbox]），这意味着
        删除一个目录及其内容；在键值后端上，则意味着删除精确的键以及所有共享
        `file_path` + "/" 前缀的键。

        Args:
            file_path: Absolute path to delete (a file, or a directory/prefix to
                remove recursively). Must start with '/'.

                要删除的绝对路径（一个文件，或要递归删除的目录/前缀），必须以
                '/' 开头。

        Returns:
            `DeleteResult` with the deleted path on success, or an error if
                nothing exists at or under the path or removal fails.

                成功时返回包含已删除路径的 `DeleteResult`，如果该路径或其下
                不存在任何内容或删除失败，则返回错误。

        Raises:
            NotImplementedError: If the backend does not implement `delete`.

            如果后端未实现 `delete`，则抛出 `NotImplementedError`。
        """
        raise NotImplementedError

    async def adelete(self, file_path: str) -> DeleteResult:
        """Async version of `delete`. / `delete` 的异步版本。"""
        return await asyncio.to_thread(self.delete, file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload multiple files to the sandbox.

        将多个文件上传到沙箱。

        This API is designed to allow developers to use it either directly or by
        exposing it to LLMs via custom tools.

        该 API 旨在让开发者既可以直接使用，也可以通过自定义工具将其暴露给 LLM。

        Args:
            files: List of (path, content) tuples to upload.

                要上传的 (path, content) 元组列表。

        Returns:
            List of `FileUploadResponse` objects, one per input file.

                每个输入文件对应一个 `FileUploadResponse` 对象列表。

                Response order matches input order (`response[i] for files[i]`).

                响应顺序与输入顺序一致（`response[i] for files[i]`）。

                Check the error field to determine success/failure per file.

                检查 error 字段以判断每个文件成功还是失败。

        Examples:
            ```python
            responses = sandbox.upload_files(
                [
                    ("/app/config.json", b"{...}"),
                    ("/app/data.txt", b"content"),
                ]
            )
            ```

            示例：将多个文件上传到沙箱。
        """
        raise NotImplementedError

    async def aupload_files(
        self, files: list[tuple[str, bytes]]
    ) -> list[FileUploadResponse]:
        """Async version of upload_files. / upload_files 的异步版本。"""
        return await asyncio.to_thread(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files from the sandbox.

        从沙箱下载多个文件。

        This API is designed to allow developers to use it either directly or
        by exposing it to LLMs via custom tools.

        该 API 旨在让开发者既可以直接使用，也可以通过自定义工具将其暴露给 LLM。

        Args:
            paths: List of file paths to download.

                要下载的文件路径列表。

        Returns:
            List of `FileDownloadResponse` objects, one per input path.

                每个输入路径对应一个 `FileDownloadResponse` 对象列表。

                Response order matches input order (`response[i] for paths[i]`).

                响应顺序与输入顺序一致（`response[i] for paths[i]`）。

                Check the error field to determine success/failure per file.

                检查 error 字段以判断每个文件成功还是失败。
        """
        raise NotImplementedError

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Async version of download_files. / download_files 的异步版本。"""
        return await asyncio.to_thread(self.download_files, paths)


@dataclass
class ExecuteResponse:
    """Result of code execution.

    Simplified schema optimized for LLM consumption.

    代码执行的结果。为 LLM 消费优化的简化 schema。
    """

    output: str
    """Combined stdout and stderr output of the executed command. / 已执行命令的 stdout 与 stderr 合并输出。"""

    exit_code: int | None = None
    """The process exit code.

    0 indicates success, non-zero indicates failure. `None` means the exit code
    could not be determined.

    进程的退出码。0 表示成功，非零表示失败。`None` 表示无法确定退出码。
    """

    truncated: bool = False
    """Whether the output was truncated due to backend limitations. / 输出是否因后端限制而被截断。"""


class ExecuteArtifact(TypedDict):
    """Machine-readable metadata attached to an `execute` tool result.

    Carried on `ToolMessage.artifact` alongside the model-facing `content`, so
    callers can react to shell failures. `artifact` is `None` instead when no
    command ran -- a validation or unsupported-backend error, where
    `ToolMessage.status` is `"error"`.

    Note that `status` is `"success"` for any command that ran, including one
    that exited non-zero: the model is expected to read the output and decide
    what to do. Use `exit_code`, not `status`, to detect command failure.

    附加到 `execute` 工具结果上的机器可读元数据。它与面向模型的 `content`
    一起放在 `ToolMessage.artifact` 上，以便调用方对 shell 失败做出反应。当没有
    命令运行时——即校验错误或不支持的后端错误，此时 `ToolMessage.status` 为
    `"error"`——`artifact` 为 `None`。

    注意，任何运行过的命令（包括以非零退出码结束的命令）其 `status` 都是
    `"success"`：模型需要读取输出并自行决定如何处理。检测命令失败应使用
    `exit_code` 而非 `status`。
    """

    exit_code: NotRequired[int]
    """The command's exit status. 0 indicates success, non-zero indicates failure.

    Omitted when the exit code could not be determined.

    命令的退出状态。0 表示成功，非零表示失败。当无法确定退出码时省略该字段。
    """


@dataclass(frozen=True, slots=True)
class ExecuteOffloadResult:
    """Result of [`BaseSandbox.execute_with_offload`][zharness.sandbox.base.BaseSandbox.execute_with_offload].

    `offloaded` describes the capture mechanism and is kept off `ExecuteResponse`
    (which an ordinary `execute` never sets).

    [`BaseSandbox.execute_with_offload`][zharness.sandbox.base.BaseSandbox.execute_with_offload]
    的结果。`offloaded` 描述捕获机制，并保持不放在 `ExecuteResponse` 上
    （普通的 `execute` 永远不会设置它）。
    """

    offloaded: bool
    """Whether the output was left at the capture path.

    When `True`, `response.output` holds only a head/tail preview and the full
    output lives at the capture path on the sandbox filesystem. When `False`,
    `response.output` is the complete output.

    输出是否留在捕获路径处。当为 `True` 时，`response.output` 仅包含头部/尾部
    预览，完整输出位于沙箱文件系统上的捕获路径。当为 `False` 时，
    `response.output` 就是完整输出。
    """

    response: ExecuteResponse
    """The command result. `response.truncated` indicates the output hit the size cap. / 命令结果。`response.truncated` 表示输出达到了大小上限。"""


class SandboxBackendProtocol(BackendProtocol):
    """Extension of `BackendProtocol` that adds shell command execution.

    Designed for backends running in isolated environments (containers, VMs,
    remote hosts).

    Adds `execute()`/`aexecute()` for shell commands and an `id` property.

    See `BaseSandbox` for a base class that implements all inherited file
    operations by delegating to `execute()`.

    `BackendProtocol` 的扩展，添加了 shell 命令执行能力。专为在隔离环境
    （容器、虚拟机、远程主机）中运行的后端设计。添加了用于 shell 命令的
    `execute()`/`aexecute()` 以及 `id` 属性。参见 `BaseSandbox`，它是一个
    通过委托给 `execute()` 实现所有继承文件操作的基础类。
    """

    @property
    def id(self) -> str:
        """Unique identifier for the sandbox backend instance. / 沙箱后端实例的唯一标识符。"""
        raise NotImplementedError

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a shell command in the sandbox environment.

        在沙箱环境中执行 shell 命令。

        Simplified interface optimized for LLM consumption.

        为 LLM 消费优化的简化接口。

        Args:
            command: Full shell command string to execute.

                要执行的完整 shell 命令字符串。

            timeout: Maximum time in seconds to wait for the command to complete.

                等待命令完成的最大时间（秒）。

                If None, uses the backend's default timeout.

                如果为 None，则使用后端的默认超时时间。

                Callers should provide non-negative integer values for portable
                behavior across backends. A value of 0 may disable timeouts on
                backends that support no-timeout execution.

                调用方应提供非负整数值，以便在各后端间获得可移植的行为。对于
                支持无超时执行的后端，值为 0 可能禁用超时。

        Returns:
            `ExecuteResponse` with combined output, exit code, and truncation flag.

            返回包含合并输出、退出码和截断标志的 `ExecuteResponse`。
        """
        raise NotImplementedError

    async def aexecute(
        self,
        command: str,
        *,
        # ASYNC109 - timeout is a semantic parameter forwarded to the sync
        # implementation, not an asyncio.timeout() contract.
        # ASYNC109 - timeout 是转发给同步实现的一个语义参数，而不是 asyncio.timeout() 契约。
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Async version of execute. / execute 的异步版本。"""
        # The middleware layer validates timeout support before calling, so
        # this guard only protects direct callers bypassing the middleware.
        # 中间件层在调用前会验证超时支持，因此该防护只保护绕过中间件的直接调用方。
        if timeout is not None and execute_accepts_timeout(type(self)):
            return await asyncio.to_thread(self.execute, command, timeout=timeout)
        return await asyncio.to_thread(self.execute, command)


@lru_cache(maxsize=256)
def _method_accepts_max_count(
    cls: type[BackendProtocol], method_name: Literal["grep", "agrep"]
) -> bool:
    """Check whether a backend method accepts the optional `max_count` keyword. / 检查后端方法是否接受可选的 `max_count` 关键字参数。"""
    try:
        sig = inspect.signature(getattr(cls, method_name))
    except (AttributeError, ValueError, TypeError):
        logger.warning(
            "Could not inspect signature of %s.%s; assuming max_count is not supported. "
            "The cap will be enforced after the search instead of bounding it, so a huge "
            "result set is fully materialized before being trimmed.",
            cls.__qualname__,
            method_name,
            exc_info=True,
        )
        return False
    return "max_count" in sig.parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in sig.parameters.values()
    )


@lru_cache(maxsize=128)
def execute_accepts_timeout(cls: type[SandboxBackendProtocol]) -> bool:
    """Check whether a backend class's `execute` accepts a `timeout` kwarg.

    Older backend packages didn't lower-bound their SDK dependency, so they
    may not accept the `timeout` keyword added to
    [`SandboxBackendProtocol`][zharness.sandbox.protocol.SandboxBackendProtocol].

    Results are cached per class to avoid repeated introspection overhead.

    检查后端类的 `execute` 是否接受 `timeout` 关键字参数。较旧的后端包没有
    对其 SDK 依赖设置下限，因此它们可能不接受添加到
    [`SandboxBackendProtocol`][zharness.sandbox.protocol.SandboxBackendProtocol]
    的 `timeout` 关键字。结果按类缓存，以避免重复的内省开销。
    """
    try:
        sig = inspect.signature(cls.execute)
    except (ValueError, TypeError):
        logger.warning(
            "Could not inspect signature of %s.execute; assuming timeout is not supported. This may indicate a backend packaging issue.",
            cls.__qualname__,
            exc_info=True,
        )
        return False
    else:
        return "timeout" in sig.parameters


def _supports_delete(backend: BackendProtocol) -> bool:
    """Check whether a backend implements `delete`.

    检查后端是否实现了 `delete`。

    `delete` is optional: backends that don't override it inherit the
    `NotImplementedError` default from
    [`BackendProtocol`][zharness.sandbox.protocol.BackendProtocol]. This
    helper lets callers detect support without invoking the method and
    triggering the error.

    `delete` 是可选的：未覆盖它的后端会从
    [`BackendProtocol`][zharness.sandbox.protocol.BackendProtocol] 继承抛出
    `NotImplementedError` 的默认实现。此辅助函数让调用方无需调用该方法、触发
    错误，即可检测是否支持。

    Args:
        backend: The backend instance to check.

            要检查的后端实例。

    Returns:
        True if the backend overrides `delete`, False otherwise.

        如果后端覆盖了 `delete` 则返回 True，否则返回 False。
    """
    return type(backend).delete is not BackendProtocol.delete
