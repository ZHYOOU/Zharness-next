"""Shared utility functions for memory backend implementations.

This module contains both user-facing string formatters and structured
helpers used by backends and the composite router. Structured helpers
enable composition without fragile string parsing.

供内存后端实现共享的实用函数。该模块包含面向用户的字符串格式化器，以及由后端与
组合路由器使用的结构化辅助函数；结构化辅助函数使组合无需脆弱的字符串解析。
"""

import functools
import logging
import os
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Final, Literal, overload

from wcmatch import glob as wcglob

from zharness.sandbox.protocol import (
    FileData,
    GrepResult,
    ReadResult,
)
from zharness.sandbox.protocol import (
    FileInfo as _FileInfo,
)
from zharness.sandbox.protocol import (
    GrepMatch as _GrepMatch,
)

logger = logging.getLogger(__name__)

EMPTY_CONTENT_WARNING = "System reminder: File exists but has empty contents"


class InvalidGlobPatternError(ValueError):
    """A glob pattern the shared matcher refuses to compile.

    Subclasses `ValueError` so existing `except ValueError` handlers keep
    working. Callers that catch this specific type can label the failure a
    *pattern* problem truthfully -- a bare `ValueError` from the same call also
    covers path normalization, and mislabeling one as the other sends the model
    off rewriting a glob that was fine.

    共享匹配器拒绝编译的 glob 模式。子类化 `ValueError`，使现有的 `except ValueError`
    处理仍能工作。捕获此特定类型的调用方可以如实将其标记为 *模式* 问题——同一调用抛出
    的裸 `ValueError` 也可能涉及路径规范化；若将二者混淆，会让模型去改写原本正常的 glob。
    """


MAX_VIDEO_INPUT_BYTES: Final = 1024 * 1024 * 1024
"""Maximum raw video payload size accepted by `read_file` frame extraction. / `read_file` 帧提取可接受的最大原始视频负载大小。"""

FileType = Literal["text", "image", "audio", "video", "file"]
"""Classification of a file by extension. / 按扩展名对文件进行的分类。"""

_EXTENSION_TO_FILE_TYPE: dict[str, FileType] = {
    # Images (https://ai.google.dev/gemini-api/docs/image-understanding) / 图片（https://ai.google.dev/gemini-api/docs/image-understanding）
    ".png": "image",
    ".jpeg": "image",
    ".jpg": "image",
    ".webp": "image",
    ".gif": "image",
    ".heic": "image",
    ".heif": "image",
    # Video (https://ai.google.dev/gemini-api/docs/video-understanding) / 视频（https://ai.google.dev/gemini-api/docs/video-understanding）
    ".mp4": "video",
    ".mpeg": "video",
    ".mov": "video",
    ".avi": "video",
    ".flv": "video",
    ".mpg": "video",
    ".webm": "video",
    ".wmv": "video",
    ".3gpp": "video",
    # Audio (https://ai.google.dev/gemini-api/docs/audio) / 音频（https://ai.google.dev/gemini-api/docs/audio）
    ".wav": "audio",
    ".mp3": "audio",
    ".aiff": "audio",
    ".aac": "audio",
    ".ogg": "audio",
    ".flac": "audio",
    # Files / 文件
    ".pdf": "file",
    ".ppt": "file",
    ".pptx": "file",
}
"""Extension-to-type mapping for non-text files.

Optional features may layer on additional classifications at the use site. For
example, `read_file` treats `.mkv` as video only when the optional video
dependencies are installed.

Derived from Google's multimodal API supported formats:

- Images: https://ai.google.dev/gemini-api/docs/image-understanding
- Video: https://ai.google.dev/gemini-api/docs/video-understanding
- Audio: https://ai.google.dev/gemini-api/docs/audio

非文本文件的扩展名到类型的映射。可选功能可在使用处叠加额外的分类。例如，`read_file`
仅在安装了可选的视频依赖时才将 `.mkv` 视为视频。源自 Google 多模态 API 支持的格式：
- 图片：https://ai.google.dev/gemini-api/docs/image-understanding
- 视频：https://ai.google.dev/gemini-api/docs/video-understanding
- 音频：https://ai.google.dev/gemini-api/docs/audio
"""

MAX_LINE_LENGTH = 5000
TOOL_RESULT_TOKEN_LIMIT = (
    20000  # Same threshold as eviction / 与驱逐（缓存淘汰）相同的阈值
)
TRUNCATION_GUIDANCE = (
    "... [results truncated, try being more specific with your parameters]"
)

# Re-export protocol types for backwards compatibility / 为向后兼容重新导出协议类型
FileInfo = _FileInfo
GrepMatch = _GrepMatch


@functools.lru_cache(maxsize=256)
def compile_grep_include_glob(pattern: str) -> Callable[[str], bool]:
    """Compile a grep include-glob into a matcher with ripgrep-like semantics.

    Provides one shared include-glob behavior for every backend so the same
    `grep(..., glob=...)` call closely mirrors ripgrep for common include
    patterns, whether or not ripgrep is installed:

    - Patterns without a `/` match the basename at any depth.

        Example: `*.py` matches `src/app/main.py`.
    - Patterns containing a `/` match the path relative to the grep search
        root, with `**` support.

        Example: `src/**/*.py` matches `src/app/main.py`.
    - A leading `/` anchors the pattern to the search root; it narrows the match
        rather than widening it.

        Example: `/*.py` matches `top.py` but not `src/app/main.py`.

    Leading-dot names match only when the pattern segment itself starts with
    `.` (no `DOTMATCH`), and `**` will not descend into dot-directories. A bare
    pattern is therefore *broader* than its `**/` form: `*.yml` matches
    `.github/workflows/ci.yml`, while `**/*.yml` does not.

    Exclusion/negation patterns (a leading `!`) are not supported: the `!` is
    treated literally rather than inverting the match, so results for such
    patterns can diverge from `rg --glob '!...'`.

    This is the single source of truth for both `grep(..., glob=...)` and
    backend `glob()`.

    Args:
        pattern: Glob include pattern.

    Returns:
        Predicate accepting a search-root-relative POSIX path; returns True when
        the path is included by `pattern`.

    Raises:
        InvalidGlobPatternError: If the pattern contains a `..` segment, or if
            `wcmatch` refuses it (e.g. brace expansion past its limit). Note
            most malformed patterns (`*.{py`, `[a-`) do not raise -- they
            compile and simply match nothing.

    将 grep 包含 glob 编译成具有类 ripgrep 语义的匹配器。为每个后端提供统一的包含
    glob 行为，使相同的 `grep(..., glob=...)` 调用在常见包含模式上尽量贴近 ripgrep，
    无论是否安装 ripgrep：
    - 不含 `/` 的模式在任何深度匹配文件名（basename）。
      示例：`*.py` 匹配 `src/app/main.py`。
    - 含 `/` 的模式匹配相对于 grep 搜索根目录的路径，并支持 `**`。
      示例：`src/**/*.py` 匹配 `src/app/main.py`。
    - 前导 `/` 将模式锚定到搜索根目录；它收窄匹配范围而非扩大。
      示例：`/*.py` 匹配 `top.py` 但不匹配 `src/app/main.py`。

    仅当模式段本身以 `.` 开头时（不使用 `DOTMATCH`）才匹配点开头的名称，且 `**`
    不会下探到点目录。因此裸模式比其 `**/` 形式更宽泛：`*.yml` 匹配
    `.github/workflows/ci.yml`，而 `**/*.yml` 不匹配。

    不支持排除/取反模式（前导 `!`）：`!` 被按字面处理而非反转匹配，因此此类模式的
    结果可能与 `rg --glob '!...'` 有差异。

    这是 `grep(..., glob=...)` 与后端 `glob()` 二者的唯一真源（single source of
    truth）。`pattern` 为 glob 包含模式；返回接受搜索根相对 POSIX 路径的谓词，当路径
    被 `pattern` 包含时返回 True。若模式包含 `..` 段，或 `wcmatch` 拒绝该模式（例如
    花括号展开超过其限制），则抛出 `InvalidGlobPatternError`。注意，大多数格式错误的
    模式（`*.{py`、`[a-`）不会抛错——它们能编译，只是匹配不到任何内容。
    """
    # Reject traversal here rather than per-backend: every backend routes
    # through this function, so a single check keeps `../*.py` from being an
    # exception in one backend and a silent empty result in another.
    # 在此处而非各个后端中拒绝路径穿越（traversal）：每个后端都经过此函数，因此一次
    # 检查即可避免 `../*.py` 在一个后端抛异常、在另一个后端却静默返回空结果。
    if ".." in pattern.replace("\\", "/").split("/"):
        msg = f"Path traversal not allowed in glob pattern {pattern!r}"
        raise InvalidGlobPatternError(msg)

    flags = wcglob.BRACE | wcglob.GLOBSTAR
    # A leading `/` anchors to the search root: strip it so it matches against
    # the (slash-less) relative path, but decide anchoring from the original
    # pattern so `/*.py` stays root-anchored instead of collapsing to a
    # basename-at-any-depth match.
    # 前导 `/` 将模式锚定到搜索根目录：将其剥离，以便与（无斜杠的）相对路径匹配；但锚定
    # 决策仍依据原始模式，使 `/*.py` 保持根锚定，而不会退化为任意深度的文件名匹配。
    anchored = "/" in pattern
    try:
        compiled = wcglob.compile(pattern.lstrip("/"), flags=flags)
    except Exception as exc:
        # `wcmatch` only raises private types (`wcmatch._wcparse.PatternLimitException`),
        # so catch broadly and re-raise a public type: every backend can then catch
        # one public type instead of importing from a private module. Log first --
        # the breadth also swallows genuine bugs (a non-`str` pattern, a wcmatch
        # version bump), which would otherwise reach the user as "invalid pattern"
        # for a pattern that is perfectly valid.
        # `wcmatch` 只抛出私有类型（`wcmatch._wcparse.PatternLimitException`），因此
        # 宽泛地捕获并重新抛出公共类型：这样每个后端只需捕获一种公共类型，而无需从私有
        # 模块导入。先记录日志——这种宽泛捕获也会吞掉真正的 bug（如非 `str` 模式、
        # wcmatch 版本升级），否则这些 bug 会让一个完全有效的模式对用户显示为
        # "invalid pattern"。
        logger.warning(
            "wcmatch refused glob pattern %r (%s): %s", pattern, type(exc).__name__, exc
        )
        msg = f"Invalid glob pattern {pattern!r}: {exc}"
        raise InvalidGlobPatternError(msg) from exc

    if anchored:

        def matcher(rel_path: str) -> bool:
            return bool(compiled.match(rel_path))
    else:

        def matcher(rel_path: str) -> bool:
            return bool(compiled.match(PurePosixPath(rel_path).name))

    return matcher


def _normalize_content(file_data: FileData) -> str:
    """Normalize current and legacy file data content to a plain string.

    Args:
        file_data: `FileData` dict with `content` key.

    Returns:
        Content as a single string.

    Raises:
        TypeError: If content is neither a string nor a legacy list of strings.

    将当前及遗留（legacy）的文件数据内容规范化为纯字符串。`file_data` 为含 `content`
    键的 `FileData` 字典；返回以单个字符串形式表示的内容。若内容既非字符串、也非遗留
    的字符串列表，则抛出 `TypeError`。
    """
    content: object = file_data["content"]
    if isinstance(content, list) and all(isinstance(line, str) for line in content):
        return "\n".join(content)
    if not isinstance(content, str):
        msg = f"File content must be a string or a legacy list of strings, got {type(content).__name__}."
        raise TypeError(msg)
    return content


def sanitize_tool_call_id(tool_call_id: str) -> str:
    r"""Sanitize tool_call_id to prevent path traversal and separator issues.

    Replaces dangerous characters (., /, \) with underscores.

    对 tool_call_id 进行净化，以防止路径穿越和分隔符问题。将危险字符（.、/、\）替换
    为下划线。
    """
    return tool_call_id.replace(".", "_").replace("/", "_").replace("\\", "_")


def format_content_with_line_numbers(
    content: str | list[str],
    start_line: int = 1,
) -> str:
    """Format file content with line numbers.

    Chunks lines longer than `MAX_LINE_LENGTH` with continuation markers
    (e.g., `5.1`, `5.2`). Line markers are separated from source content
    with two spaces so source tabs cannot be confused with a gutter separator.

    Args:
        content: File content as string or list of lines
        start_line: Starting line number

    Returns:
        Formatted content with line numbers and continuation markers

    为文件内容添加行号进行格式化。对超过 `MAX_LINE_LENGTH` 的行进行分块，并使用续行
    标记（如 `5.1`、`5.2`）。行标记与源内容之间用两个空格分隔，以免源文本中的制表符
    与装订线分隔符混淆。`content` 为文件内容（字符串或行列表），`start_line` 为起始
    行号；返回带行号与续行标记的格式化内容。
    """
    if isinstance(content, str):
        lines = content.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
    else:
        lines = content

    rows: list[tuple[str, str]] = []
    marker_width = 0
    for i, line in enumerate(lines):
        line_num = i + start_line
        # One slice per MAX_LINE_LENGTH chunk; short lines yield a single chunk.
        # `or [line]` keeps a row for a blank line, whose empty range would
        # otherwise drop it, so it still gets a gutter.
        # 每个 MAX_LINE_LENGTH 块对应一次切片；短行只产生一个块。`or [line]` 为空白行
        # 保留一行，否则其空范围会将其丢弃，使其仍能获得装订线。
        chunks = [
            line[s : s + MAX_LINE_LENGTH] for s in range(0, len(line), MAX_LINE_LENGTH)
        ] or [line]

        for chunk_idx, chunk in enumerate(chunks):
            marker = str(line_num) if chunk_idx == 0 else f"{line_num}.{chunk_idx}"
            rows.append((marker, chunk))
            marker_width = max(marker_width, len(marker))

    # The two-space marker/source separator is a load-bearing contract shared by
    # two downstream parsers that must stay in sync with the separator emitted
    # here:
    #   - `ReadFileContinuationNoticeMiddleware._is_numbered_read_file_row`
    #     (profiles/harness/_nvidia_nemotron_3_ultra.py) counts source rows to
    #     decide whether to append the continuation notice.
    #   - `ToolCallMessage._compact_line_gutter` (the TUI, in a
    #     separate package: libs/code/.../tui/widgets/messages.py) re-justifies
    #     the gutter for display.
    # Both also tolerate the legacy `cat -n` tab. Shrinking this separator below
    # two spaces (or otherwise diverging) would silently break them; the
    # producer->consumer round-trip tests in both packages guard against that.
    # 两个空格的行标记/源内容分隔符是一个承重契约（load-bearing contract），由两个下游
    # 解析器共享，它们必须与此处产生的分隔符保持同步：
    #   - `ReadFileContinuationNoticeMiddleware._is_numbered_read_file_row`
    #     （profiles/harness/_nvidia_nemotron_3_ultra.py）统计源行数以决定是否附加续行提示。
    #   - `ToolCallMessage._compact_line_gutter`（位于独立包中的 TUI：
    #     libs/code/.../tui/widgets/messages.py）为显示重新对齐装订线。
    # 两者也都兼容遗留的 `cat -n` 制表符。将此分隔符缩小到两个空格以下（或以其他方式
    # 偏离）会静默破坏它们；两个包中的生产者→消费者往返测试对此加以防护。
    return "\n".join(f"{marker:>{marker_width}}  {line}" for marker, line in rows)


def check_empty_content(content: str) -> str | None:
    """Check if content is empty and return warning message.

    Args:
        content: Content to check

    Returns:
        Warning message if empty, `None` otherwise

    检查内容是否为空并返回警告信息。`content` 为待检查内容；为空时返回警告信息，否则
    返回 `None`。
    """
    if not content or content.strip() == "":
        return EMPTY_CONTENT_WARNING
    return None


def _get_file_type(path: str) -> FileType:
    """Classify a file by its extension.

    Args:
        path: File path to classify.

    Returns:
        One of `"text"`, `"image"`, `"audio"`, `"video"`, or `"file"`.

            Defaults to `"text"` for unrecognized extensions.

    按文件扩展名对文件进行分类。`path` 为待分类的文件路径；返回 `"text"`、`"image"`、
    `"audio"`、`"video"` 或 `"file"` 之一。无法识别的扩展名默认为 `"text"`。
    """
    return _EXTENSION_TO_FILE_TYPE.get(PurePosixPath(path).suffix.lower(), "text")


_VIDEO_EXTRA_EXTENSIONS: frozenset[str] = frozenset({".mkv"})
"""Video container extensions handled outside the Google-derived multimodal map.

These are intentionally absent from `_EXTENSION_TO_FILE_TYPE`, so a `read_file`
without the optional `[video]` extra returns them as a generic file block rather
than a native video block. Backends must still read them as binary — never
text-decode them — and `read_file` layers frame extraction on top only when the
`[video]` dependencies are installed.

在 Google 衍生的多模态映射之外处理的视频容器扩展名。这些扩展名有意不出现在
`_EXTENSION_TO_FILE_TYPE` 中，因此未安装可选的 `[video]` 附加依赖时，`read_file`
会将它们作为通用文件块而非原生视频块返回。后端仍必须将其作为二进制读取——绝不能按
文本解码——且仅当安装了 `[video]` 依赖时，`read_file` 才在其上叠加帧提取。
"""


def _get_backend_read_file_type(path: str) -> FileType:
    """Classify a file for backend reads, forcing known video containers to binary.

    Backends decide binary-vs-text on `_get_file_type(...) != "text"`. Extensions
    in `_VIDEO_EXTRA_EXTENSIONS` are absent from `_EXTENSION_TO_FILE_TYPE`, so
    `_get_file_type` alone would treat them as text and corrupt the bytes (a raw
    UTF-8 decode of a video, or line-slicing a base64 blob). Classify them as
    `"video"` here so the binary read path runs on every backend.

    Args:
        path: File path to classify.

    Returns:
        `"video"` for `_VIDEO_EXTRA_EXTENSIONS`; otherwise the shared
            `_get_file_type` classification.

    对后端读取的文件进行分类，强制将已知视频容器视为二进制。后端以
    `_get_file_type(...) != "text"` 判断二进制或文本。`_VIDEO_EXTRA_EXTENSIONS`
    中的扩展名不在 `_EXTENSION_TO_FILE_TYPE` 中，因此仅凭 `_get_file_type` 会将其当作
    文本并损坏字节（对视频进行原始 UTF-8 解码，或对 base64 块进行按行切片）。在此将其
    归类为 `"video"`，使二进制读取路径在所有后端上都能运行。`path` 为待分类的文件路径；
    `_VIDEO_EXTRA_EXTENSIONS` 中的返回 `"video"`，否则返回共享的 `_get_file_type`
    分类结果。
    """
    if PurePosixPath(path).suffix.lower() in _VIDEO_EXTRA_EXTENSIONS:
        return "video"
    return _get_file_type(path)


def file_data_to_string(file_data: FileData) -> str:
    """Convert current or legacy persisted file content to a string.

    Args:
        file_data: File data whose content is a string or legacy list of strings.

    Returns:
        Content as a single string.

    Raises:
        TypeError: If content is neither a string nor a legacy list of strings.

    将当前或遗留（legacy）的持久化文件内容转换为字符串。`file_data` 的内容为字符串或
    遗留的字符串列表；返回单个字符串。若内容既非字符串也非遗留的字符串列表，则抛出
    `TypeError`。
    """
    return _normalize_content(file_data)


def create_file_data(
    content: str,
    created_at: str | None = None,
    encoding: str = "utf-8",
) -> FileData:
    """Create a `FileData` object with timestamps.

    Args:
        content: File content as string (plain text or base64-encoded binary).
        created_at: Optional creation timestamp (ISO format).
        encoding: Content encoding — `"utf-8"` for text, `"base64"` for binary.

    Returns:
        FileD`ata dict with content, encoding, and timestamps.

    创建带时间戳的 `FileData` 对象。`content` 为文件内容（纯文本或 base64 编码的二进制）；
    `created_at` 为可选的创建时间戳（ISO 格式）；`encoding` 为内容编码——文本用
    `"utf-8"`，二进制用 `"base64"`。返回含 content、encoding 与时间戳的 `FileData`
    字典。
    """
    now = datetime.now(UTC).isoformat()

    return {
        "content": content,
        "encoding": encoding,
        "created_at": created_at or now,
        "modified_at": now,
    }


def update_file_data(file_data: FileData, content: str) -> FileData:
    """Update `FileData` with new content, preserving creation timestamp.

    Args:
        file_data: Existing `FileData` dict
        content: New content as string

    Returns:
        Updated `FileData` dict

    用新内容更新 `FileData`，保留创建时间戳。`file_data` 为现有的 `FileData` 字典，
    `content` 为新内容字符串；返回更新后的 `FileData` 字典。
    """
    now = datetime.now(UTC).isoformat()

    result = FileData(
        content=content,
        encoding=file_data.get("encoding", "utf-8"),
    )
    if "created_at" in file_data:
        result["created_at"] = file_data["created_at"]
    result["modified_at"] = now
    return result


def _copy_file_data_with_content(file_data: FileData, content: str) -> FileData:
    """Clone `file_data` with replaced content, preserving timestamps when present.

    Unlike `update_file_data`, this carries `created_at`/`modified_at` through
    verbatim rather than restamping `modified_at`, since slicing a read window
    does not mutate the underlying file.

    Args:
        file_data: Source `FileData` whose encoding and timestamps are copied.
        content: Replacement content for the returned copy.

    Returns:
        A new `FileData` with `content` set and metadata carried over.

    克隆 `file_data` 并替换其内容，存在时间戳时予以保留。与 `update_file_data` 不同，
    此函数会原样保留 `created_at`/`modified_at`，而不是重新盖 `modified_at` 时间戳，
    因为切分读取窗口并不会改变底层文件。`file_data` 为源 `FileData`（复制其编码与
    时间戳），`content` 为返回副本的替换内容；返回设置了 `content` 并携带原元数据的
    新 `FileData`。
    """
    sliced_fd = FileData(
        content=content,
        encoding=file_data.get("encoding", "utf-8"),
    )
    if "created_at" in file_data:
        sliced_fd["created_at"] = file_data["created_at"]
    if "modified_at" in file_data:
        sliced_fd["modified_at"] = file_data["modified_at"]
    return sliced_fd


def normalize_read_bounds(offset: int, limit: int) -> tuple[int, int]:
    """Floor a requested read window at a zero offset and zero lines.

    Models occasionally emit degenerate `read_file` arguments (`offset=-1`,
    `limit=0`). Clamping `offset` keeps backends from reporting a line range
    that starts before line 1, which `ReadResult` rejects.

    Clamping `limit` is *not* sufficient on its own: flooring a negative limit
    at `0` produces a zero-length window, which still has no valid
    `start_line`/`end_line` pair. Callers must additionally treat a returned
    `limit` of `0` as an empty read — see `slice_read_response` below, or the
    equivalent short-circuits in the sandbox and LangSmith backends, which
    flag the result with `ReadResult.no_lines_requested`.

    The `int()` coercion is deliberate and load-bearing, not redundant with the
    annotations: `offset` and `limit` originate from model-supplied tool
    arguments, and the sandbox backend interpolates them into the source of a
    script it executes (`_READ_COMMAND_TEMPLATE`). Do not remove it.

    Args:
        offset: Requested 0-indexed line offset.
        limit: Requested maximum number of lines.

    Returns:
        Tuple of `(offset, limit)`, each coerced to `int` and floored at `0`.

    将请求的读取窗口下限设为偏移量 0 与行数 0。模型偶尔会发出退化的 `read_file` 参数
    （`offset=-1`、`limit=0`）。钳制 `offset` 可避免后端返回从第 1 行之前开始的行范围，
    而这是 `ReadResult` 所拒绝的。

    仅钳制 `limit` 并不足够：将负数 limit 下限设为 `0` 会产生零长度的窗口，它仍没有
    有效的 `start_line`/`end_line` 组合。调用方还必须把返回的 `limit` 为 `0` 视为空
    读取——参见下方的 `slice_read_response`，或 sandbox 与 LangSmith 后端中的等效短路
    逻辑，它们用 `ReadResult.no_lines_requested` 标记结果。

    `int()` 强转是有意为之且承重（load-bearing）的，并非与注解重复：`offset` 和
    `limit` 来自模型提供的工具参数，且 sandbox 后端会将其插入到所执行脚本的源码中
    （`_READ_COMMAND_TEMPLATE`）。切勿移除它。`offset` 为请求的 0 起始行偏移，`limit`
    为请求的最大行数；返回 `(offset, limit)` 元组，二者均强转为 `int` 并在 `0` 处设
    下限。
    """
    normalized_offset, normalized_limit = max(int(offset), 0), max(int(limit), 0)
    if (normalized_offset, normalized_limit) != (offset, limit):
        logger.debug(
            "Clamped degenerate read window: offset %r -> %d, limit %r -> %d",
            offset,
            normalized_offset,
            limit,
            normalized_limit,
        )
    return normalized_offset, normalized_limit


def slice_read_response(
    file_data: FileData,
    offset: int,
    limit: int,
) -> ReadResult:
    """Slice file data to the requested line range without formatting.

    The returned `ReadResult` carries the raw (unformatted) window in
    `file_data`; line-number formatting is applied downstream by the
    middleware layer.

    Args:
        file_data: `FileData` dict.
        offset: Line offset (0-indexed).
        limit: Maximum number of lines.

    Both bounds are clamped through `normalize_read_bounds` before slicing, so
    a negative `offset` reads from the first line and a negative `limit` is
    treated as `0`.

    Returns:
        `ReadResult` with the sliced raw content and pagination metadata
            (`total_lines`, `start_line`, `end_line`, `next_offset`). The
            pagination fields are left unset for empty or whitespace-only
            content, and when the clamped `limit` is `0`; the zero-`limit`
            result additionally sets `no_lines_requested` so the middleware
            can tell the never-inspected window apart from a genuinely empty
            file. `error` is set instead when the offset exceeds the file
            length.

    在不格式化的情况下将文件数据切分为请求的行范围。返回的 `ReadResult` 在 `file_data`
    中携带原始（未格式化）窗口；行号格式化由下游中间件层完成。`file_data` 为 `FileData`
    字典，`offset` 为行偏移（0 起始），`limit` 为最大行数。

    两个边界在切分前都会经 `normalize_read_bounds` 钳制，因此负的 `offset` 从第一行
    开始读取，负的 `limit` 按 `0` 处理。

    返回携带切片后原始内容与分页元数据（`total_lines`、`start_line`、`end_line`、
    `next_offset`）的 `ReadResult`。对于空内容或纯空白内容，以及当钳制后的 `limit` 为
    `0` 时，分页字段保持未设置；零 `limit` 的结果还会额外设置 `no_lines_requested`，
    使中间件能将从未被检查的窗口与真正为空的文件区分开。当偏移量超过文件长度时，则改
    为设置 `error`。
    """
    content = file_data_to_string(file_data)
    offset, limit = normalize_read_bounds(offset, limit)

    # Ordering note: blank content is reported before the zero-limit check, so a
    # whitespace-only file returns its content (which the middleware maps to the
    # empty-file reminder) rather than `""`, regardless of `limit`.
    # 顺序说明：空内容在零 limit 检查之前先被报告，因此纯空白的文件会返回其内容（中间件
    # 将其映射为空文件提醒），而不是返回 `""`，无论 `limit` 为何值。
    if not content or content.strip() == "":
        return ReadResult(file_data=_copy_file_data_with_content(file_data, content))

    # Nothing was requested: flag the window as never inspected so the
    # middleware can tell it apart from a genuinely empty file, which arrives
    # via the blank-content branch above (its `ReadResult` is otherwise
    # identical: empty content, no pagination metadata).
    # 未请求任何内容：将该窗口标记为从未被检查，使中间件能将其与真正为空的文件区分开；
    # 后者经上面的空内容分支到达（其 `ReadResult` 在其他方面完全相同：空内容、无分页
    # 元数据）。
    if limit == 0:
        return ReadResult(
            file_data=_copy_file_data_with_content(file_data, ""),
            no_lines_requested=True,
        )

    # `splitlines(keepends=True)` retains each line's terminator, including
    # the absence of one on the final line. Joining with `""` therefore
    # round-trips the trailing-newline state of the file faithfully —
    # required so `edit()` can report EOF-newline mismatches accurately. It
    # also splits on CR / CRLF, so line indexing matches the LF-normalized
    # form without first rewriting the whole (potentially huge) string.
    # `splitlines(keepends=True)` 会保留每一行的行终止符，包括最后一行没有终止符的情况。
    # 因此用 `""` 拼接能忠实还原文件的行尾换行状态——这是 `edit()` 准确报告 EOF 换行
    # 不一致所必需的。它也会按 CR / CRLF 切分，因此行索引与 LF 规范化后的形式一致，而
    # 无需先重写整个（可能很大的）字符串。
    lines = content.splitlines(keepends=True)
    start_idx = offset
    end_idx = min(start_idx + limit, len(lines))
    total_lines = len(lines)

    if start_idx >= total_lines:
        return ReadResult(
            error=f"Line offset {offset} exceeds file length ({total_lines} lines)"
        )

    # Normalize line endings to LF, but only across the requested window.
    # State/Store backends may carry CRLF or CR content as written;
    # downstream tooling (edit match, grep, format) assumes LF.
    # 仅在整个请求窗口内将行尾规范化为 LF。State/Store 后端可能按原样携带 CRLF 或 CR
    # 内容；下游工具（edit 匹配、grep、format）假定为 LF。
    sliced = "".join(lines[start_idx:end_idx]).replace("\r\n", "\n").replace("\r", "\n")
    next_offset = end_idx if end_idx < total_lines else None
    return ReadResult(
        file_data=_copy_file_data_with_content(file_data, sliced),
        total_lines=total_lines,
        start_line=start_idx + 1,
        end_line=end_idx,
        next_offset=next_offset,
    )


def perform_string_replacement(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, int] | str:
    """Perform string replacement with occurrence validation.

    Args:
        content: Original content
        old_string: String to replace
        new_string: Replacement string
        replace_all: Whether to replace all occurrences

    Returns:
        Tuple of `(new_content, occurrences)` on success, or error message string

    执行带出现次数校验的字符串替换。`content` 为原始内容，`old_string` 为要替换的
    字符串，`new_string` 为替换后的字符串，`replace_all` 指示是否替换所有出现；成功时
    返回 `(new_content, occurrences)` 元组，否则返回错误消息字符串。
    """
    occurrences = content.count(old_string)

    if occurrences == 0:
        # Detect a common EOF mismatch: `old_string` carries a trailing
        # newline that the file lacks at the same position. Models infer a
        # terminator on what looks like a "well-formed" line; exact-match
        # consumers must surface a precise hint rather than silently relax
        # the contract — silent recovery on a stripped key risks corrupting
        # interior text that happens to share a prefix.
        # 检测一种常见的 EOF 不一致：`old_string` 带有尾部换行，而文件在相同位置没有。
        # 模型会对看似"格式良好的"行推断出终止符；精确匹配的使用方必须给出精确的提示，
        # 而不是静默放宽契约——对剥离后的键静默恢复可能损坏恰好共享前缀的内部文本。
        if (
            old_string.endswith("\n")
            and len(old_string) > 1
            and content.endswith(old_string.removesuffix("\n"))
        ):
            stripped = old_string.removesuffix("\n")
            stripped_count = content.count(stripped)
            if stripped_count == 1:
                return (
                    "Error: old_string ends with a newline, but the file does "
                    "not end with a newline. Retry with the trailing newline "
                    "removed from old_string (and from new_string if it also "
                    "ends with a newline)."
                )
            # Stripped key is ambiguous: the model needs both fixes at once
            # (drop the newline AND add surrounding context).
            # 剥离后的键具有歧义：模型需要同时做两处修正（去掉换行并且添加上下文）。
            return (
                f"Error: old_string ends with a newline, but the file does "
                f"not end with a newline. With the trailing newline removed, "
                f"old_string would appear {stripped_count} times in the file. "
                f"Retry with the trailing newline removed and add surrounding "
                f"context so the match is unique."
            )
        return f"Error: String not found in file: '{old_string}'"

    if occurrences > 1 and not replace_all:
        return (
            f"Error: String '{old_string}' appears {occurrences} times in file. "
            f"Use replace_all=True to replace all instances, or provide a more specific string with surrounding context."
        )

    new_content = content.replace(old_string, new_string)
    return new_content, occurrences


@overload
def truncate_if_too_long(result: list[str]) -> list[str]: ...


@overload
def truncate_if_too_long(result: str) -> str: ...


def truncate_if_too_long(result: list[str] | str) -> list[str] | str:
    """Truncate list or string result if it exceeds token limit (rough estimate: 4 chars/token). / 如果列表或字符串结果超过 token 上限则进行截断（粗略估计：4 字符/token）。"""
    if isinstance(result, list):
        total_chars = sum(len(item) for item in result)
        if total_chars > TOOL_RESULT_TOKEN_LIMIT * 4:
            return result[
                : len(result) * TOOL_RESULT_TOKEN_LIMIT * 4 // total_chars
            ] + [
                TRUNCATION_GUIDANCE
            ]  # Concatenation preferred for clarity / 为清晰起见优先采用拼接
        return result
    # string / 字符串
    if len(result) > TOOL_RESULT_TOKEN_LIMIT * 4:
        return result[: TOOL_RESULT_TOKEN_LIMIT * 4] + "\n" + TRUNCATION_GUIDANCE
    return result


# Characters that mark a glob path component as a wildcard segment for the
# purposes of `_glob_anchor`. Keep in sync with the wcmatch flags used by the
# filesystem middleware (`BRACE | GLOBSTAR`).
# 用于 `_glob_anchor` 时将 glob 路径组件标记为通配符段的字符。需与文件系统中间件使用
# 的 wcmatch 标志（`BRACE | GLOBSTAR`）保持同步。
_GLOB_WILDCARD_CHARS = frozenset("*?[{")


def _glob_anchor(pattern: str) -> str:
    """Return the longest leading directory of `pattern` with no wildcards.

    For `/secrets/**` returns `/secrets`; for `/a/*/b` returns `/a`; for a
    pattern with a wildcard at or near the root (`/**/secrets`, `/*/foo`)
    falls back to `/`. The root fallback causes overlap checks to match
    *any* subtree — conservative over-gating, since we cannot statically
    pin down where the rule could resolve. Callers wanting precise gating
    should anchor the rule's leading components.

    返回 `pattern` 中最长的不含通配符的前导目录。对于 `/secrets/**` 返回 `/secrets`；
    对于 `/a/*/b` 返回 `/a`；对于在根目录或靠近根目录处含通配符的模式（`/**/secrets`、
    `/*/foo`）则回退到 `/`。根目录回退会使重叠检查匹配 *任意* 子树——这是一种保守的
    过度限制，因为我们无法静态确定规则会解析到何处。希望精确限制的调用方应锚定规则的
    前导组件。
    """
    parts = PurePosixPath(to_posix_path(pattern)).parts
    safe: list[str] = []
    for part in parts:
        if any(c in _GLOB_WILDCARD_CHARS for c in part):
            break
        safe.append(part)
    if not safe:
        return "/"
    return str(PurePosixPath(*safe))


def _paths_overlap(call_path: str, rule_anchor: str) -> bool:
    """Return True if the subtree at `call_path` intersects the subtree at `rule_anchor`.

    Two subtrees overlap when one is a (component-wise) prefix of the other,
    or they're equal. Comparison runs on `PurePosixPath` components, so
    `/secret` does not overlap `/secrets`. The root `/` overlaps everything.

    若 `call_path` 处的子树与 `rule_anchor` 处的子树相交则返回 True。当两个子树之一
    是另一个的（按组件计的）前缀，或二者相等时，它们重叠。比较在 `PurePosixPath`
    组件上进行，因此 `/secret` 与 `/secrets` 不重叠。根 `/` 与所有内容重叠。
    """
    a = PurePosixPath(call_path)
    b = PurePosixPath(rule_anchor)
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def to_posix_path(path: str) -> str:
    r"""Normalize backslash separators to forward slashes for `PurePosixPath` use.

    Backends running on Windows return OS-native paths using backslashes.
    `PurePosixPath` treats backslashes as literal filename characters,
    so `PurePosixPath(r"C:\a\b").name` yields the full string instead
    of `"b"`. Normalize before constructing a `PurePosixPath`.

    This is best-effort: a POSIX directory literally named with a backslash
    will also be rewritten. That trade-off is accepted because such filenames
    are vanishingly rare in practice and the alternative (gating on `os.sep`)
    fails when a Windows-style path is handed to a non-Windows process.

    Args:
        path: Path string that may use backslash separators.

    Returns:
        The same path with every `\\` replaced by `/`.

            Inputs that already use forward slashes are returned unchanged.

    为 `PurePosixPath` 使用而将反斜杠分隔符规范化为正斜杠。在 Windows 上运行的后端
    会返回使用反斜杠的 OS 原生路径。`PurePosixPath` 将反斜杠视为文件名中的字面字符，
    因此 `PurePosixPath(r"C:\a\b").name` 会得到完整字符串而不是 `"b"`。在构造
    `PurePosixPath` 之前先做规范化。

    这是尽力而为的做法：实际名为反斜杠的 POSIX 目录也会被改写。之所以接受这一权衡，
    是因为这类文件名在实践中极为罕见，而替代方案（依据 `os.sep` 判断）在将 Windows
    风格路径交给非 Windows 进程时会失效。`path` 为可能使用反斜杠分隔符的路径字符串；
    返回每个 `\\` 都被替换为 `/` 的同一路径。已使用正斜杠的输入将原样返回。
    """
    return path.replace("\\", "/")


def validate_path(path: str, *, allowed_prefixes: Sequence[str] | None = None) -> str:
    r"""Validate and normalize file path for security.

    Ensures paths are safe to use by preventing directory traversal attacks
    and enforcing consistent formatting. All paths are normalized to use
    forward slashes and start with a leading slash.

    This function is designed for virtual filesystem paths and rejects
    Windows absolute paths (e.g., `C:/...`, `F:/...`) to maintain consistency
    and prevent path format ambiguity.

    Args:
        path: The path to validate and normalize.
        allowed_prefixes: Optional list of allowed path prefixes.

            If provided, the normalized path must start with one of
            these prefixes.

    Returns:
        Normalized canonical path starting with `/` and using forward slashes.

    Raises:
        ValueError: If path contains traversal sequences (`..` or `~`), is a
            Windows absolute path (e.g., `C:/...`), or does not start with an
            allowed prefix when `allowed_prefixes` is specified.

    Example:
        ```python
        validate_path("foo/bar")  # Returns: "/foo/bar" / 返回："/foo/bar"
        validate_path("/./foo//bar")  # Returns: "/foo/bar" / 返回："/foo/bar"
        validate_path("../etc/passwd")  # Raises ValueError / 抛出 ValueError
        validate_path(r"C:\\Users\\file.txt")  # Raises ValueError / 抛出 ValueError
        validate_path("/data/file.txt", allowed_prefixes=["/data/"])  # OK / 通过
        validate_path("/etc/file.txt", allowed_prefixes=["/data/"])  # Raises ValueError / 抛出 ValueError
        ```
    出于安全目的校验并规范化文件路径。通过防止目录穿越攻击并强制一致的格式，确保路径
    安全可用。所有路径都规范化为使用正斜杠并以前导斜杠开头。

    此函数专为虚拟文件系统路径设计，并拒绝 Windows 绝对路径（如 `C:/...`、`F:/...`），
    以保持一致并防止路径格式歧义。`path` 为待校验和规范化的路径；`allowed_prefixes`
    为可选的前导路径前缀列表（若提供，规范化后的路径必须以其中一个前缀开头）。返回以
    `/` 开头、使用正斜杠的规范化规范路径。若路径含穿越序列（`..` 或 `~`）、是 Windows
    绝对路径（如 `C:/...`），或在指定 `allowed_prefixes` 时未以允许的前缀开头，则抛出
    `ValueError`。
    """
    # Check for traversal as a path component (not substring) to avoid
    # false-positive rejection of legitimate filenames like "foo..bar.txt"
    # 以路径组件（而非子串）的形式检查穿越，以避免误拒诸如 "foo..bar.txt" 之类的合法
    # 文件名。
    parts = PurePosixPath(to_posix_path(path)).parts
    if ".." in parts or path.startswith("~"):
        msg = f"Path traversal not allowed: {path}"
        raise ValueError(msg)

    # Reject Windows absolute paths (e.g., C:\..., D:/...) / 拒绝 Windows 绝对路径（例如 C:\...、D:/...）
    if re.match(r"^[a-zA-Z]:", path):
        msg = f"Windows absolute paths are not supported: {path}. Please use virtual paths starting with / (e.g., /workspace/file.txt)"
        raise ValueError(msg)

    normalized = os.path.normpath(path)
    normalized = normalized.replace("\\", "/")

    if not normalized.startswith("/"):
        normalized = f"/{normalized}"

    # Defense-in-depth: verify normpath didn't produce traversal / 纵深防御：验证 normpath 没有产生路径穿越
    if ".." in normalized.split("/"):
        msg = f"Path traversal detected after normalization: {path} -> {normalized}"
        raise ValueError(msg)

    if allowed_prefixes is not None and not any(
        normalized.startswith(prefix) for prefix in allowed_prefixes
    ):
        msg = f"Path must start with one of {allowed_prefixes}: {path}"
        raise ValueError(msg)

    return normalized


def _normalize_path(path: str | None) -> str:
    """Normalize a path to canonical form.

    Converts path to absolute form starting with /, removes trailing slashes
    (except for root), and validates that the path is not empty.

    Args:
        path: Path to normalize (None defaults to "/")

    Returns:
        Normalized path starting with / (without trailing slash unless it's root)

    Raises:
        ValueError: If path is invalid (empty string after strip)

    Example:
        _normalize_path(None) -> "/"
        _normalize_path("/dir/") -> "/dir"
        _normalize_path("dir") -> "/dir"
        _normalize_path("/") -> "/"

    将路径规范化为规范形式。将路径转换为以 / 开头的绝对形式，移除尾部斜杠（根目录除外），
    并校验路径非空。`path` 为待规范化的路径（`None` 默认为 "/"）；返回以 / 开头的规范化
    路径（除非是根目录，否则无尾部斜杠）。若路径无效（剥离后为空字符串），则抛出
    `ValueError`。
    """
    path = path or "/"
    if not path or path.strip() == "":
        msg = "Path cannot be empty"
        raise ValueError(msg)

    normalized = path if path.startswith("/") else "/" + path

    # Only root should have trailing slash / 只有根目录应有尾部斜杠
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")

    return normalized


def _filter_files_by_path(
    files: dict[str, Any], normalized_path: str
) -> dict[str, Any]:
    """Filter files dict by normalized path, handling exact file matches and directory prefixes.

    Expects a normalized path from `_normalize_path` (no trailing slash except root).

    Args:
        files: Dictionary mapping file paths to file data
        normalized_path: Normalized path from `_normalize_path` (e.g., "/", "/dir", "/dir/file")

    Returns:
        Filtered dictionary of files matching the path

    Example:
        files = {"/dir/file": {...}, "/dir/other": {...}}
        _filter_files_by_path(files, "/dir/file")  # Returns {"/dir/file": {...}} / 返回 {"/dir/file": {...}}
        _filter_files_by_path(files, "/dir")       # Returns both files / 返回两个文件

    按规范化路径过滤文件字典，处理精确文件匹配与目录前缀。期望来自 `_normalize_path`
    的规范化路径（除根目录外无尾部斜杠）。`files` 为文件路径到文件数据的映射字典，
    `normalized_path` 为来自 `_normalize_path` 的规范化路径（如 "/"、"/dir"、
    "/dir/file"）；返回与路径匹配的过滤后的文件字典。
    """
    # Check if path matches an exact file / 检查路径是否精确匹配某个文件
    if normalized_path in files:
        return {normalized_path: files[normalized_path]}

    # Otherwise treat as directory prefix / 否则按目录前缀处理
    if normalized_path == "/":
        # Root directory - match all files starting with / / 根目录——匹配所有以 / 开头的文件
        return {fp: fd for fp, fd in files.items() if fp.startswith("/")}
    # Non-root directory - add trailing slash for prefix matching / 非根目录——添加尾部斜杠以进行前缀匹配
    dir_prefix = normalized_path + "/"
    return {fp: fd for fp, fd in files.items() if fp.startswith(dir_prefix)}


def _relative_to_root(file_path: str, normalized_path: str) -> str:
    """Return `file_path` relative to a normalized grep/glob search root.

    Args:
        file_path: Absolute file path (e.g. "/src/app/main.py").
        normalized_path: Normalized search root from `_normalize_path`.

    Returns:
        POSIX path relative to the search root (e.g. "src/app/main.py").

            When `file_path` equals the search root (an exact-file search),
            returns just the basename.

    返回 `file_path` 相对于规范化 grep/glob 搜索根目录的路径。`file_path` 为绝对文件
    路径（如 "/src/app/main.py"），`normalized_path` 为来自 `_normalize_path` 的规范化
    搜索根目录；返回相对于搜索根目录的 POSIX 路径（如 "src/app/main.py"）。当
    `file_path` 等于搜索根目录（精确文件搜索）时，仅返回文件名。
    """
    if normalized_path == "/":
        return file_path[1:]
    if file_path == normalized_path:
        return file_path.rsplit("/", maxsplit=1)[-1]
    return file_path[len(normalized_path) + 1 :]


def _glob_search_files(
    files: dict[str, Any],
    pattern: str,
    path: str | None = None,
) -> str:
    r"""Search files dict for paths matching glob pattern.

    Uses the shared backend contract from `compile_grep_include_glob`:

    - Patterns without `/` match the basename at any depth under `path`.
    - Patterns containing `/` match paths relative to `path`, with `**` support.
    - A leading `/` anchors to the search root (narrows, does not widen).

    Args:
        files: Dictionary of file paths to FileData.
        pattern: Glob pattern (e.g., `"*.py"`, `"**/*.ts"`, `"src/**/*.py"`).
        path: Base path to search from. `None` defaults to root.

    Returns:
        Newline-separated file paths, sorted by modification time (most recent first).

            `"No files found"` if no matches.

    Raises:
        InvalidGlobPatternError: If the matcher refuses `pattern` (see
            `compile_grep_include_glob`). Note an unparseable `path` is *not*
            raised -- it returns `"No files found"`.

    Example:
        ```python
        files = {"/src/main.py": FileData(...), "/test.py": FileData(...)}
        _glob_search_files(files, "*.py", "/")
        # Returns: "/test.py\n/src/main.py" (sorted by modified_at) / 返回："/test.py\n/src/main.py"（按 modified_at 排序）
        ```

    在文件字典中搜索与 glob 模式匹配的路径。使用来自 `compile_grep_include_glob` 的共享
    后端契约：
    - 不含 `/` 的模式在 `path` 下任意深度匹配文件名。
    - 含 `/` 的模式匹配相对于 `path` 的路径，并支持 `**`。
    - 前导 `/` 锚定到搜索根目录（收窄而非扩大）。

    `files` 为文件路径到 FileData 的字典，`pattern` 为 glob 模式（如 `"*.py"`、
    `"**/*.ts"`、`"src/**/*.py"`），`path` 为搜索的基础路径（`None` 默认根目录）。
    返回按修改时间排序（最近优先）的换行分隔文件路径；若无匹配则返回 `"No files found"`。
    若匹配器拒绝 `pattern`（参见 `compile_grep_include_glob`）则抛出
    `InvalidGlobPatternError`。注意不可解析的 `path` 不会抛错——它返回 `"No files found"`。
    """
    try:
        normalized_path = _normalize_path(path)
    except ValueError:
        return "No files found"

    filtered = _filter_files_by_path(files, normalized_path)
    matcher = compile_grep_include_glob(pattern)

    matches = []
    for file_path, file_data in filtered.items():
        # Compute relative path for glob matching
        # If normalized_path is "/dir", we want "/dir/file.txt" -> "file.txt"
        # If normalized_path is "/dir/file.txt" (exact file), we want "file.txt"
        # 计算用于 glob 匹配的相对路径。
        # 若 normalized_path 为 "/dir"，我们希望 "/dir/file.txt" -> "file.txt"。
        # 若 normalized_path 为 "/dir/file.txt"（精确文件），我们希望 "file.txt"。
        relative = _relative_to_root(file_path, normalized_path)

        if matcher(relative):
            matches.append((file_path, file_data["modified_at"]))

    matches.sort(key=lambda x: x[1], reverse=True)

    if not matches:
        return "No files found"

    return "\n".join(fp for fp, _ in matches)


def _format_grep_results(
    results: dict[str, list[tuple[int, str]]],
    output_mode: Literal["files_with_matches", "content", "count"],
) -> str:
    """Format grep search results based on output mode.

    Args:
        results: Dictionary mapping file paths to list of `(line_num, line_content)` tuples
        output_mode: Output format

    Returns:
        Formatted string output

    根据输出模式格式化 grep 搜索结果。`results` 为文件路径到 `(line_num, line_content)`
    元组列表的字典映射，`output_mode` 为输出格式；返回格式化后的字符串输出。
    """
    if output_mode == "files_with_matches":
        return "\n".join(sorted(results.keys()))
    if output_mode == "count":
        lines = []
        for file_path in sorted(results.keys()):
            count = len(results[file_path])
            lines.append(f"{file_path}: {count}")
        return "\n".join(lines)
    lines = []
    for file_path in sorted(results.keys()):
        lines.append(f"{file_path}:")
        for line_num, line in results[file_path]:
            lines.append(f"  {line_num}: {line}")
    return "\n".join(lines)


# -------- Structured helpers for composition -------- / -------- 面向组合的结构化辅助函数 --------


def grep_matches_from_files(
    files: dict[str, Any],
    pattern: str,
    path: str | None = None,
    glob: str | None = None,
    *,
    max_count: int | None = None,
) -> GrepResult:
    """Return structured grep matches from an in-memory files mapping.

    Performs literal text search (not regex).

    Returns a `GrepResult` with matches on success. When `max_count` is set, at
    most that many matches are returned; if more exist the scan stops and the
    result is flagged `truncated=True`. Exactly `max_count` matches with none
    dropped is reported complete (`truncated=False`).

    We deliberately do not raise here to keep backends non-throwing in tool
    contexts and preserve user-facing error messages: a refused `glob` filter
    is returned as `GrepResult(error=...)`, not raised.

    从内存中的文件映射返回结构化的 grep 匹配结果。执行字面文本搜索（非正则）。成功时
    返回带匹配的 `GrepResult`。当设置了 `max_count` 时，至多返回那么多条匹配；若存在
    更多匹配，则停止扫描并将结果标记为 `truncated=True`。恰好 `max_count` 条匹配且未
    丢弃任何一条时报告为完成（`truncated=False`）。

    我们有意在此不抛异常，以使后端在工具上下文中保持不抛错并保留面向用户的错误消息：
    被拒绝的 `glob` 过滤器以 `GrepResult(error=...)` 形式返回，而非抛出。
    """
    try:
        normalized_path = _normalize_path(path)
    except ValueError:
        return GrepResult(matches=[])

    filtered = _filter_files_by_path(files, normalized_path)

    if glob:
        try:
            matcher = compile_grep_include_glob(glob)
        except InvalidGlobPatternError as exc:
            return GrepResult(error=str(exc))
        filtered = {
            fp: fd
            for fp, fd in filtered.items()
            if matcher(_relative_to_root(fp, normalized_path))
        }

    matches: list[GrepMatch] = []
    for file_path, file_data in filtered.items():
        content_str = _normalize_content(file_data)
        for line_num, line in enumerate(content_str.split("\n"), 1):
            if (
                pattern in line
            ):  # Simple substring search for literal matching / 字面匹配的简单子串搜索
                if max_count is not None and len(matches) >= max_count:
                    # A further match beyond `max_count` proves more exist; stop
                    # and flag truncation. Checked before appending so exactly
                    # `max_count` matches is reported complete, not truncated.
                    # 超出 `max_count` 的进一步匹配证明还有更多存在；停止并标记截断。
                    # 在追加之前检查，因此恰好 `max_count` 条匹配会被报告为完成而非截断。
                    return GrepResult(matches=matches, truncated=True)
                matches.append({"path": file_path, "line": int(line_num), "text": line})
    return GrepResult(matches=matches)


def build_grep_results_dict(
    matches: list[GrepMatch],
) -> dict[str, list[tuple[int, str]]]:
    """Group structured matches into the legacy dict form used by formatters. / 将结构化匹配分组为格式化器使用的遗留字典形式。"""
    grouped: dict[str, list[tuple[int, str]]] = {}
    for m in matches:
        grouped.setdefault(m["path"], []).append((m["line"], m["text"]))
    return grouped


def format_grep_matches(
    matches: list[GrepMatch],
    output_mode: Literal["files_with_matches", "content", "count"],
) -> str:
    """Format structured grep matches using existing formatting logic. / 使用现有的格式化逻辑格式化结构化的 grep 匹配。"""
    if not matches:
        return "No matches found"

    # Presence of the context keys signals "context mode" for the whole result;
    # the producer sets both keys on every match or none. `_format_grep_with_context`
    # still tolerates a hand-built mix of matches with and without context, because
    # `format_grep_matches` is public and may be handed such input.
    # 上下文字键的出现标志着整个结果处于"上下文模式"；生产者要么在每条匹配上设置两个
    # 键，要么都不设置。`_format_grep_with_context` 仍容忍手工构造的、含与不含上下文的
    # 混合匹配，因为 `format_grep_matches` 是公开函数，可能收到此类输入。
    if output_mode != "content" or not any(
        "context_before" in match or "context_after" in match for match in matches
    ):
        return _format_grep_results(build_grep_results_dict(matches), output_mode)
    return _format_grep_with_context(matches)


def _format_grep_with_context(matches: list[GrepMatch]) -> str:
    """Render `content`-mode grep output including surrounding context lines.

    Matched lines are marked with `:` and context lines with `-`. Non-adjacent
    line groups within a file are separated by a `--` line, mirroring `grep -C`.

    渲染包含周围上下文行的 `content` 模式 grep 输出。匹配行用 `:` 标记，上下文行用
    `-` 标记。文件内不相邻的行组之间用 `--` 行分隔，对应 `grep -C`。
    """
    matches_by_path: dict[str, list[GrepMatch]] = {}
    for match in matches:
        matches_by_path.setdefault(match["path"], []).append(match)

    lines: list[str] = []
    for file_path in sorted(matches_by_path):
        file_matches = matches_by_path[file_path]
        matching_lines = {match["line"] for match in file_matches}
        displayed_lines: dict[int, str] = {}
        for match in file_matches:
            for context_line in match.get("context_before", []):
                displayed_lines[context_line["line"]] = context_line["text"]
            displayed_lines[match["line"]] = match["text"]
            for context_line in match.get("context_after", []):
                displayed_lines[context_line["line"]] = context_line["text"]

        lines.append(f"{file_path}:")
        for group_index, group in enumerate(_group_adjacent_lines(displayed_lines)):
            if group_index:
                lines.append("  --")
            for line_num, text in group:
                separator = ":" if line_num in matching_lines else "-"
                lines.append(f"  {line_num}{separator} {text}")
    return "\n".join(lines)


def _group_adjacent_lines(
    displayed_lines: dict[int, str],
) -> list[list[tuple[int, str]]]:
    """Split `{line_number: text}` into runs of consecutive line numbers. / 将 `{line_number: text}` 拆分为连续行号的序列。"""
    groups: list[list[tuple[int, str]]] = []
    for item in sorted(displayed_lines.items()):
        if not groups or item[0] > groups[-1][-1][0] + 1:
            groups.append([item])
        else:
            groups[-1].append(item)
    return groups


_REGEX_SIGNAL_RE = re.compile(
    r"\|"  # alternation
    r"|\.\*"  # `.*` wildcard
    r"|\.\+"  # `.+` wildcard
    r"|\\[.wWdDsSbB(){}\[\]|+*?^$]"  # escaped regex metacharacters / classes
)
"""Strong signals that a pattern was written as a regex rather than literal text.

Deliberately conservative: bare `.`, `(`, `)`, `[`, `]`, `?`, `^`, `$` are
omitted because they appear routinely in literal code searches (e.g.
`self.tools`, `def __init__(self):`, `arr[0]`), which would cause false hints.

模式是作为正则而非字面文本编写的强信号。有意保持保守：裸的 `.`、`(`、`)`、`[`、`]`、
`?`、`^`、`$` 被省略，因为它们经常出现在字面代码搜索中（如 `self.tools`、
`def __init__(self):`、`arr[0]`），否则会产生错误提示。
"""


def _looks_like_regex(pattern: str) -> bool:
    """Heuristically detect regex syntax in a pattern meant for literal grep. / 启发式地检测本用于字面 grep 的模式中的正则语法。"""
    return bool(_REGEX_SIGNAL_RE.search(pattern))


def regex_literal_hint(pattern: str) -> str | None:
    """Return a hint when a pattern looks like an (unsupported) regex.

    `grep` matches literal text, so regex metacharacters are searched verbatim
    and silently miss. Callers gate this on a no-match result; the function
    itself only inspects the pattern.

    Args:
        pattern: The literal grep pattern to inspect for regex signals.

    Returns:
        A one-line hint steering the caller toward literal search, or `None`
            when the pattern has no regex signals.

    当模式看起来像是（不支持的）正则时返回一条提示。`grep` 匹配字面文本，因此正则元
    字符会被逐字搜索并静默漏配。调用方在无匹配结果时触发此函数；函数本身只检查模式。
    `pattern` 为待检查正则信号的字面 grep 模式；返回引导调用方使用字面搜索的单行提示，
    当模式没有正则信号时返回 `None`。
    """
    if not _looks_like_regex(pattern):
        return None
    return (
        "Note: grep matches literal text, not regex, so characters like "
        "`|`, `.*`, and `\\.` are searched verbatim. Search for the literal "
        "text you need instead; for `|` alternation, run a separate search "
        "per alternative."
    )
