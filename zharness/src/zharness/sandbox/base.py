"""Base sandbox implementation.

[`BaseSandbox`][zharness.sandbox.base.BaseSandbox] implements
[`SandboxBackendProtocol`][zharness.sandbox.protocol.SandboxBackendProtocol].

File listing, grep, glob, and read use shell commands via `execute()`. Write
delegates content transfer to `upload_files()`. Edit uses server-side `execute()`
for payloads under `_EDIT_INLINE_MAX_BYTES` and falls back to uploading old/new
strings as temp files with a server-side replace script for larger ones.

Concrete subclasses implement `execute()` and `upload_files()`; all other
operations are derived from those.

BaseSandbox 基类实现。实现
[`SandboxBackendProtocol`][zharness.sandbox.protocol.SandboxBackendProtocol]。

文件列举、grep、glob 与读取通过 `execute()` 使用 shell 命令。写入委托给
`upload_files()` 传输内容。编辑在负载小于 `_EDIT_INLINE_MAX_BYTES` 时使用服务端
`execute()`；更大的负载则把 old/new 字符串作为临时文件上传，再由服务端替换脚本处理。

具体子类实现 `execute()` 和 `upload_files()`；其余操作均由二者派生。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shlex
from abc import ABC, abstractmethod
from typing import Any, Final, Literal

from zharness.sandbox.protocol import (
    ASYNC_GLOB_TIMEOUT,
    ASYNC_GREP_TIMEOUT,
    DeleteResult,
    EditResult,
    ExecuteOffloadResult,
    ExecuteResponse,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GlobTruncationReason,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
    execute_accepts_timeout,
)
from zharness.utils import _get_backend_read_file_type, normalize_read_bounds

logger = logging.getLogger(__name__)

_GLOB_COMMAND_TEMPLATE = """python3 -c "
import fnmatch
import os
import json
import base64
import time

# Decode base64-encoded parameters / 解码 base64 编码的参数
path = base64.b64decode('{path_b64}').decode('utf-8')
pattern = base64.b64decode('{pattern_b64}').decode('utf-8')

# Bounds on a model-supplied pattern over an untrusted tree. Exceeding any of
# them sets the truncated flag on the result rather than failing. TIME_BUDGET is
# the sandbox-side analogue of _DEFAULT_GLOB_TIMEOUT in filesystem.py and is
# deliberately the same 5s; the outer round-trip is bounded separately by
# ASYNC_GLOB_TIMEOUT. (No backticks: this comment runs through sh.)
# 对模型提供的、作用于不可信目录树的模式施加界限。超过任何一项都会在结果上设置
# truncated 标志而不是直接失败。TIME_BUDGET 是 filesystem.py 中
# _DEFAULT_GLOB_TIMEOUT 的沙箱侧对应量，刻意保持同样的 5 秒；外层往返由
# ASYNC_GLOB_TIMEOUT 单独限界。（不用反引号：本注释会经由 sh 执行。）
MAX_EXPANSIONS = 1000
MAX_MATCHES = 10000
TIME_BUDGET = 5.0


def _find_group_end(pat, start):
    # Index of the '}}' closing the group opened at 'start', or -1 if unbalanced. / 'start' 处开组的 '}}' 的闭合索引，若不平衡则为 -1。
    depth = 0
    for index in range(start, len(pat)):
        if pat[index] == '{{':
            depth += 1
        elif pat[index] == '}}':
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_alternatives(body):
    # Split on top-level commas only, so nested groups survive intact. / 仅在顶层逗号处切分，从而嵌套组保持完整。
    parts = []
    depth = 0
    current = ''
    for ch in body:
        if ch == '{{':
            depth += 1
            current += ch
        elif ch == '}}':
            depth -= 1
            current += ch
        elif ch == ',' and depth == 0:
            parts.append(current)
            current = ''
        else:
            current += ch
    parts.append(current)
    return parts


def _brace_expand(pat):
    # pattern is model/user supplied, so expansion must be bounded: the full
    # Cartesian product is materialized in memory, and 2**n groups would
    # otherwise hang or OOM the sandbox before the walk starts. Returns None
    # past the budget, mirroring the expansion limit wcmatch enforces in
    # compile_grep_include_glob. Nested groups expand like wcmatch's BRACE.
    # 模式由模型/用户提供，因此展开必须受限：完整笛卡尔积会在内存中物化，
    # 2**n 个组否则会在遍历开始前让沙箱挂起或耗尽内存。超出预算时返回 None，
    # 与 wcmatch 在 compile_grep_include_glob 中施加的展开上限一致。嵌套组按
    # wcmatch 的 BRACE 方式展开。
    start = pat.find('{{')
    if start < 0:
        return [pat]
    end = _find_group_end(pat, start)
    if end < 0:
        return [pat]
    prefix, body, suffix = pat[:start], pat[start + 1 : end], pat[end + 1 :]
    parts = _split_alternatives(body)
    if len(parts) < 2:
        # A single-element group is literal, but the rest may still expand. / 单元素组按字面处理，但其余部分仍可能继续展开。
        tails = _brace_expand(suffix)
        if tails is None:
            return None
        return [prefix + '{{' + body + '}}' + tail for tail in tails]
    out = []
    for part in parts:
        tails = _brace_expand(part + suffix)
        if tails is None:
            return None
        for tail in tails:
            out.append(prefix + tail)
            if len(out) > MAX_EXPANSIONS:
                return None
    return out


def _normalize_classes(pat):
    # fnmatch reads a leading '^' in a bracket expression as a literal, while
    # wcmatch (and bash/ripgrep) read it as negation. Without this rewrite
    # '[^a]*.py' is inverted on the sandbox: it returns exactly the files the
    # caller meant to exclude.
    # fnmatch 将括号表达式开头的 '^' 按字面处理，而 wcmatch（以及 bash/ripgrep）
    # 将其视为取反。不做此改写时，'[^a]*.py' 在沙箱上会被反转：返回的正是调用方
    # 想要排除的那些文件。
    out = ''
    index = 0
    while index < len(pat):
        if pat[index] != '[':
            out += pat[index]
            index += 1
            continue
        # Start at index + 2 so a literal ']' first in the set is kept ('[]]'). / 从 index + 2 开始，从而保留集合开头的字面 ']'（'[]]'）。
        close = pat.find(']', index + 2)
        if close < 0:
            out += pat[index:]
            break
        body = pat[index + 1 : close]
        if body.startswith('^'):
            body = '!' + body[1:]
        out += '[' + body + ']'
        index = close + 1
    return out


def _basename_match(name, candidates):
    for candidate in candidates:
        # No DOTMATCH: leading-dot basenames need an explicit leading '.' pattern. / 无 DOTMATCH：以点开头的文件名需要显式的开头 '.' 模式。
        if name.startswith('.') and not candidate.startswith('.'):
            continue
        # fnmatchcase, not fnmatch: fnmatch applies os.path.normcase, which would
        # make matching case-insensitive on a non-POSIX host. wcmatch is always
        # case-sensitive here.
        # 用 fnmatchcase 而非 fnmatch：fnmatch 会应用 os.path.normcase，在非 POSIX
        # 主机上会使匹配大小写不敏感。wcmatch 在此处始终区分大小写。
        if fnmatch.fnmatchcase(name, candidate):
            return True
    return False


def _parts_match(rel_parts, pat_parts):
    # Memoized on (path index, pattern index): '**' otherwise backtracks
    # exponentially, so '**/*/**/*'-shaped patterns hang the sandbox.
    # 以（路径索引，模式索引）做记忆化：否则 '**' 会指数级回溯，
    # '**/*/**/*' 形状的模式会拖垮沙箱。
    cache = {{}}

    def match_from(ri, pi):
        key = (ri, pi)
        if key in cache:
            return cache[key]
        result = _compute(ri, pi)
        cache[key] = result
        return result

    def _compute(ri, pi):
        while pi < len(pat_parts):
            if pat_parts[pi] == '**':
                while pi < len(pat_parts) and pat_parts[pi] == '**':
                    pi += 1
                if pi == len(pat_parts):
                    # A slash before a trailing ** requires at least one
                    # descendant; a.py/** must not match the file a.py.
                    # 结尾 ** 之前的斜杠要求至少有一个后代；
                    # a.py/** 不得匹配文件 a.py。
                    return ri < len(rel_parts) and all(not part.startswith('.') for part in rel_parts[ri:])
                while ri <= len(rel_parts):
                    if match_from(ri, pi):
                        return True
                    if ri == len(rel_parts):
                        break
                    # ** without DOTMATCH does not traverse leading-dot segments. / 无 DOTMATCH 时，** 不会遍历以点开头的路径段。
                    if rel_parts[ri].startswith('.'):
                        return False
                    ri += 1
                return False
            if ri >= len(rel_parts):
                return False
            name = rel_parts[ri]
            seg = pat_parts[pi]
            if name.startswith('.') and not seg.startswith('.'):
                return False
            if not fnmatch.fnmatchcase(name, seg):
                return False
            ri += 1
            pi += 1
        return ri == len(rel_parts)

    return match_from(0, 0)


def _path_match(rel, candidates):
    rel_parts = [] if rel in ('', '.') else [seg for seg in rel.split('/') if seg]
    for candidate in candidates:
        relative_candidate = candidate.lstrip('/')
        segments = relative_candidate.split('/')
        # Drop empty segments so 'a//b.py' matches 'a/b.py', as wcmatch does. / 丢弃空段，使 'a//b.py' 匹配 'a/b.py'，与 wcmatch 一致。
        pat_parts = [seg for seg in segments if seg]
        # A trailing slash means directory-only, and only regular files are
        # emitted -- except after '**', which absorbs it.
        # 结尾斜杠表示仅匹配目录，且只输出常规文件——但 '**' 之后的除外，它会吸收该斜杠。
        if len(segments) > 1 and segments[-1] == '' and (not pat_parts or pat_parts[-1] != '**'):
            continue
        if _parts_match(rel_parts, pat_parts):
            return True
    return False


def _include_match(rel, pat, candidates):
    # Shared backend contract (same idea as compile_grep_include_glob):
    # - no '/' -> basename at any depth (including under hidden dirs)
    # - with '/' -> path-relative, ** supported, leading '/' anchors after lstrip
    # 共享的后端约定（与 compile_grep_include_glob 思路相同）：
    # - 无 '/' -> 任意深度的 basename（包括隐藏目录之下）
    # - 有 '/' -> 相对路径，支持 **，前导 '/' 在 lstrip 后作为锚点
    if '/' not in pat:
        name = rel.rsplit('/', 1)[-1]
        return _basename_match(name, candidates)
    return _path_match(rel, candidates)


walk_errors = []

# Pseudo-filesystems are effectively infinite and never hold user files. A bare
# pattern is basename-at-any-depth, so a search rooted at '/' would otherwise
# burn the entire time budget in /proc and return an arbitrary prefix.
# 伪文件系统实际上无限且从不存放用户文件。裸模式是任意深度的 basename 匹配，
# 因此以 '/' 为根的搜索否则会耗尽全部时间预算于 /proc，并返回任意的前缀。
PRUNE_AT_ROOT = ('proc', 'sys', 'dev')


def _on_walk_error(err):
    # Keep the failing path, not just the exception class: 'PermissionError' x40
    # cannot distinguish one chronically unreadable mount from an unreadable tree.
    # 保留失败的路径，而不仅是异常类：40 次 'PermissionError' 无法区分一个长期
    # 不可读的挂载点与一棵不可读的目录树。
    walk_errors.append(type(err).__name__ + ':' + str(getattr(err, 'filename', '?')))


def _emit(matches, truncated):
    for item in sorted(matches):
        print(json.dumps({{'path': item, 'is_dir': False}}))
    if walk_errors:
        print(json.dumps({{
            'warning': 'walk_errors',
            'count': len(walk_errors),
            'sample': walk_errors[:5],
        }}))
    if truncated:
        print(json.dumps({{'warning': 'truncated'}}))


matches = []
truncated = False
# Prologue: everything that can fail before any match exists. Kept in its own
# try so its handlers only ever fire when there is genuinely nothing to report --
# a failure raised from inside the walk below must not be reported as an
# inaccessible search root while discarding thousands of good matches.
# 序幕：在产生任何匹配之前所有可能失败的步骤。单独放在自己的 try 中，使其处理分支
# 只在确实没有任何可报告内容时触发——下方遍历内部抛出的失败绝不能一边丢弃成千上万
# 条良好匹配，一边被报告为“搜索根不可访问”。
ready = False
try:
    real_root = os.path.realpath(path)
    # os.path.realpath('/') is '/', so a naive real_root + os.sep is '//', which
    # no absolute path starts with. Normalize, or a search rooted at '/' (the
    # default when no path is passed) silently drops every match.
    # os.path.realpath('/') 为 '/'，因此朴素的 real_root + os.sep 得到 '//'，
    # 而没有任何绝对路径以它开头。需归一化，否则以 '/' 为根的搜索（未传路径时的默认值）
    # 会静默丢弃全部匹配。
    root_prefix = real_root if real_root.endswith(os.sep) else real_root + os.sep
    os.chdir(path)
    if any(seg == '..' for seg in pattern.replace(chr(92), '/').split('/')):
        print(json.dumps({{'error': 'invalid_pattern'}}))
    else:
        expanded = _brace_expand(pattern)
        if expanded is None:
            print(json.dumps({{'error': 'pattern_too_broad'}}))
        else:
            candidates = [_normalize_classes(item) for item in expanded]
            ready = True
except FileNotFoundError:
    print(json.dumps({{'error': 'path_not_found'}}))
except NotADirectoryError:
    print(json.dumps({{'error': 'not_a_directory'}}))
except PermissionError:
    print(json.dumps({{'error': 'permission_denied'}}))
except Exception as exc:
    # Without this, any other failure reaches stdout as a traceback that the
    # host parser cannot read, and the caller sees a successful empty search.
    # Carry the message, bounded: 'internal_error: KeyError' alone cannot
    # distinguish a pattern-parser bug from a surrogate in a filename.
    # 没有此处理时，任何其他失败都会以宿主解析器无法读取的 traceback 形式进入
    # stdout，调用方将看到一次“成功但为空”的搜索。携带限长的消息：仅凭
    # 'internal_error: KeyError' 无法区分模式解析器的 bug 与文件名中的代理字符。
    print(json.dumps({{'error': 'internal_error: ' + type(exc).__name__ + ': ' + str(exc)[:200]}}))

if ready:
    deadline = time.monotonic() + TIME_BUDGET
    at_root = real_root == os.sep
    try:
        # os.walk includes hidden directories; matching rules still exclude
        # leading-dot basenames unless the pattern is explicit (no DOTMATCH).
        # onerror is required: os.walk otherwise discards unreadable subtrees
        # silently, shrinking the result set with no signal to the caller.
        # os.walk 会包含隐藏目录；匹配规则仍会排除以点开头的 basename，
        # 除非模式显式给出（无 DOTMATCH）。onerror 必不可少：否则 os.walk 会静默
        # 丢弃不可读的子树，在未向调用方发出任何信号的情况下缩小结果集。
        for dirpath, dirnames, filenames in os.walk('.', onerror=_on_walk_error):
            if truncated:
                break
            if dirpath == '.' and at_root:
                dirnames[:] = [d for d in dirnames if d not in PRUNE_AT_ROOT]
            if time.monotonic() > deadline:
                truncated = True
                break
            for name in filenames:
                if time.monotonic() > deadline or len(matches) >= MAX_MATCHES:
                    truncated = True
                    break
                full = name if dirpath == '.' else os.path.join(dirpath, name)
                rel = full.replace(chr(92), '/')
                if rel.startswith('./'):
                    rel = rel[2:]
                if not _include_match(rel, pattern, candidates):
                    continue
                candidate = os.path.realpath(full)
                if candidate != real_root and not candidate.startswith(root_prefix):
                    continue
                # Regular files only, mirroring LocalSandbox.glob's
                # is_file() filter; also drops broken symlinks.
                # 仅返回常规文件，与 LocalSandbox.glob 的 is_file() 过滤一致；同时丢弃损坏的符号链接。
                if not os.path.isfile(candidate):
                    continue
                matches.append(rel)
    except Exception as exc:
        # A failure mid-walk (a symlink racing a deletion, an unreadable entry
        # os.walk raises rather than routing to onerror) must not throw away the
        # matches already found. Record it as a walk error and emit the partial
        # set -- valid but incomplete, which is exactly what 'walk_errors' means.
        # 遍历中途的失败（符号链接与删除竞争、os.walk 抛出而非交给 onerror 的
        # 不可读条目）绝不能丢弃已找到的匹配。将其记为 walk error 并输出部分结果集
        # ——有效但不完整，这正是 'walk_errors' 的含义。
        walk_errors.append(type(exc).__name__ + ':' + str(exc)[:100])
    _emit(matches, truncated)

" 2>&1"""
"""Find files matching a pattern.

Uses base64-encoded parameters to avoid shell escaping issues. Walks the search
tree with `os.walk` (including hidden directories) and applies the shared
basename/path glob contract so bare `*.py` matches nested files under hidden
dirs while still excluding leading-dot basenames unless the pattern is explicit.

Emits one JSON object per matching regular file (directories are omitted, as in
`LocalSandbox.glob`), then an out-of-band `warning` record when the walk
was cut short by its time/match budget or skipped an unreadable subtree, so a
partial result is never mistaken for an exhaustive one. `walk_errors` and
`truncated` are separate warnings because they need different remedies: the
first cannot be fixed by narrowing the search, the second can.

Every failure *inside the script* emits a structured `error` code rather than a
traceback. Failures before it runs (no `python3`, a shell-level error) and output
the transport clips still arrive as raw text, which `_parse_glob_output` treats
as a hard error.

按模式查找文件。

使用 base64 编码的参数以避免 shell 转义问题。用 `os.walk` 遍历搜索树（包含隐藏目录），
并应用共享的 basename/路径 glob 约定，使裸 `*.py` 能匹配隐藏目录下的嵌套文件，
同时仍排除以点开头的 basename，除非模式显式给出。

每个匹配的常规文件输出一个 JSON 对象（目录被省略，与 `LocalSandbox.glob` 一致），
随后当遍历因时间/匹配预算被截断或跳过了不可读的子树时，输出一条带外 `warning`
记录，使部分结果永远不会被误认为穷举结果。`walk_errors` 与 `truncated` 是两条
独立的警告，因为它们的处置不同：前者无法通过收窄搜索修复，后者可以。

脚本*内部*的任何失败都会输出结构化的 `error` 码而非 traceback。脚本运行前的失败
（没有 `python3`、shell 级错误）以及被传输层截断的输出仍以原始文本到达，
`_parse_glob_output` 会将其视为硬错误。
"""


_GREP_PATH_GLOB_TEMPLATE = """python3 -c "
import glob, os, base64, sys

search_path = base64.b64decode('{path_b64}').decode('utf-8')
glob_pat = base64.b64decode('{glob_b64}').decode('utf-8')
pattern = base64.b64decode('{pattern_b64}').decode('utf-8')
max_count = {max_count}
match_count = 0

# When the search path is a directory, chdir to it so glob patterns
# resolve relative to it. When it is a single file, search it directly
# (glob filtering is irrelevant for a single-file search).
# 当搜索路径是目录时，chdir 到该目录，使 glob 模式相对它解析。
# 当它是单个文件时，直接搜索该文件（单文件搜索与 glob 过滤无关）。
if os.path.isdir(search_path):
    os.chdir(search_path)
    # A leading slash would make glob.glob treat the pattern as an
    # absolute filesystem path, searching outside the search root (e.g.
    # /*.py after chdir('/workspace') would match /top.py on
    # the host, not /workspace/top.py). Strip it so anchored globs
    # stay relative to the search root, matching the LocalSandbox
    # semantics where slash anchors to the root, not the filesystem.
    # 前导斜杠会使 glob.glob 将模式当作绝对文件系统路径，从而搜索到搜索根之外
    # （例如 chdir('/workspace') 后的 /*.py 会匹配宿主上的 /top.py，而非
    # /workspace/top.py）。剥离它，使带锚点的 glob 保持相对于搜索根，符合
    # LocalSandbox 的语义：斜杠锚定到根，而非整个文件系统。
    rel_glob = glob_pat.lstrip('/')
    if any(seg == '..' for seg in rel_glob.replace(chr(92), '/').split('/')):
        sys.stderr.write('glob contains path traversal\\n')
        sys.exit(2)
    real_root = os.path.realpath(search_path)
    rel_files = sorted(glob.glob(rel_glob, recursive=True))
    # Open the glob-relative path (cwd is the search root) but report the
    # path prefixed with the search root, so GrepResult.path matches the
    # root/match form that grep -r emits on the --include route.
    # 打开 glob 相对路径（cwd 即搜索根），但报告时加上搜索根前缀，
    # 使 GrepResult.path 与 grep -r 在 --include 路径上输出的“根/匹配”形式一致。
    targets = []
    for rel in rel_files:
        real_open = os.path.realpath(rel)
        if real_open != real_root and not real_open.startswith(real_root + os.sep):
            continue
        display_path = os.path.join(search_path, os.path.relpath(real_open, real_root))
        targets.append((real_open, display_path))
else:
    targets = [(search_path, search_path)]

for open_path, display_path in targets:
    try:
        with open(open_path, 'r', encoding='utf-8', errors='ignore') as fh:
            for i, line in enumerate(fh, 1):
                if pattern in line:
                    # GNU grep -HnFZ always terminates each record with a
                    # newline, even when the matched line has none. Strip
                    # the line's own trailing newline and add an explicit
                    # one so records never concatenate when a file's last
                    # line lacks a final newline.
                    # GNU grep -HnFZ 总是以换行结束每条记录，即使匹配行本身没有。
                    # 去掉该行自身的尾部换行并显式补一个，使记录在文件末行缺少
                    # 结尾换行时也不会粘连。
                    sys.stdout.write(display_path + chr(0) + str(i) + ':' + line.rstrip(chr(10)) + chr(10))
                    match_count += 1
                    # Emit one record past the cap (match_count > max_count, not
                    # >=) so the parser can tell "exactly at the cap" (complete)
                    # from "capped early" (truncated). Mirrors the head -n
                    # max_count+1 route in _build_grep_cmd.
                    # 输出一条超出上限的记录（match_count > max_count 而非 >=），
                    # 使解析器能区分“恰在上限”（完整）与“提前截断”（truncated）。
                    # 与 _build_grep_cmd 中 head -n max_count+1 的做法一致。
                    if max_count is not None and match_count > max_count:
                        sys.exit(0)
    except OSError:
        pass
" 2>/dev/null"""
"""Search file contents for a literal string, filtered by a path-relative glob.

Used when the glob pattern contains a `/` (e.g. `src/**/*.py`), because
GNU `grep --include` only matches basenames and would silently return zero
results for such patterns. All three parameters are base64-encoded to avoid
shell escaping issues.

Emits the same `path\0line_num:text` record structure that `grep -HnFZ`
produces — each match path is prefixed with the search root to mirror
grep's output — so `_parse_grep_output` consumes it unchanged. Unlike the
`grep -r` route, results are sorted, hidden files and directories are
skipped (Python `glob` semantics), and file contents are decoded as UTF-8
with `errors='ignore'` rather than matched byte-for-byte.

`stderr` is discarded, but `|| true` is deliberately omitted: the script
exits 0 on a legitimate no-match, so a non-zero exit signals a genuine
failure (bad base64, an inaccessible search root) that `_parse_grep_output`
surfaces as an error instead of a silent empty result.

搜索文件内容中的字面字符串，并按路径相对 glob 过滤。

当 glob 模式包含 `/`（如 `src/**/*.py`）时使用此路径，因为 GNU `grep --include`
只匹配 basename，对这类模式会静默返回零结果。三个参数都经过 base64 编码，
以避免 shell 转义问题。

输出与 `grep -HnFZ` 产生的 `path\0line_num:text` 相同的记录结构——每个匹配路径
都加上搜索根前缀以镜像 grep 的输出——因此 `_parse_grep_output` 可原样消费。
与 `grep -r` 路径不同，结果已排序，跳过隐藏文件与目录（Python `glob` 语义），
文件内容按 UTF-8 解码（`errors='ignore'`）而非逐字节匹配。

丢弃 `stderr`，但刻意省略 `|| true`：脚本在合法无匹配时以 0 退出，
因此非零退出码标志着真正的失败（base64 错误、搜索根不可访问），
`_parse_grep_output` 将其作为错误呈现，而非静默的空结果。
"""


_WRITE_CHECK_TEMPLATE = """python3 -c "
import os, base64

path = base64.b64decode('{path_b64}').decode('utf-8')
os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
" 2>&1"""
"""Preflight for write operations: create parent directories for the target path if it doesn't exist.

Only the (small) base64-encoded path is interpolated — file content is
transferred separately via `upload_files()`.

写入操作的预检：若目标路径的父目录不存在，则创建之。

只插值（较小的）base64 编码路径——文件内容通过 `upload_files()` 单独传输。
"""

MAX_BINARY_BYTES: Final = 500 * 1024
"""Maximum size of a binary file returned by `read()` as base64.

Files exceeding this size return a `Binary file exceeds maximum preview size`
error rather than being base64-encoded in full. Backends overriding `read()`
should import and reuse this constant to stay in sync with the base
implementation. Kept in lockstep with the `MAX_BINARY_BYTES` literal in
`_READ_COMMAND_TEMPLATE` (asserted by `test_read_constants_match_template`).

`read()` 以 base64 形式返回的二进制文件的最大大小。

超过此大小的文件返回 `Binary file exceeds maximum preview size` 错误，
而不是完整地 base64 编码。覆写 `read()` 的后端应导入并复用此常量，
以与基类实现保持同步。与 `_READ_COMMAND_TEMPLATE` 中的 `MAX_BINARY_BYTES`
字面量保持联动（由 `test_read_constants_match_template` 断言）。
"""

MAX_OUTPUT_BYTES: Final = 500 * 1024
"""Maximum size of rendered text content returned by `read()`.

Pages exceeding this cap are truncated and `TRUNCATION_MSG` is appended.
Mirrors the `MAX_OUTPUT_BYTES` literal in `_READ_COMMAND_TEMPLATE`.

`read()` 返回的渲染文本内容的最大大小。

超过此上限的页会被截断并追加 `TRUNCATION_MSG`。
与 `_READ_COMMAND_TEMPLATE` 中的 `MAX_OUTPUT_BYTES` 字面量保持一致。
"""

TRUNCATION_MSG: Final = (
    "\n\n[Output was truncated due to size limits. "
    "This paginated read result exceeded the sandbox stdout limit. "
    "Continue reading with a larger offset or smaller limit to inspect the rest of the file.]"
)
"""Sentinel appended to `read()` content when `MAX_OUTPUT_BYTES` is hit. / 当达到 `MAX_OUTPUT_BYTES` 时追加到 `read()` 内容末尾的哨兵文本。"""

_EDIT_COMMAND_TEMPLATE = """python3 -c "
import sys, os, stat as _stat, base64, json

payload = json.loads(base64.b64decode(sys.stdin.read().strip()).decode('utf-8'))
path, old, new = payload['path'], payload['old'], payload['new']
replace_all = payload.get('replace_all', False)

try:
    st = os.stat(path)
    if not _stat.S_ISREG(st.st_mode):
        print(json.dumps({{'error': 'not_a_file'}}))
        sys.exit(0)

    with open(path, 'rb') as f:
        raw = f.read()

    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        print(json.dumps({{'error': 'not_a_text_file'}}))
        sys.exit(0)

    # Match-driven CRLF handling (issue #2880): the read template normalizes
    # CRLF to LF for the LLM, so old_string arrives LF-only even when the
    # file on disk is CRLF. Try old as sent, then a CRLF variant, then an LF
    # variant. The first match reveals the file line-ending style in that
    # region; apply the same transform to new so the file style is preserved.
    # 基于匹配驱动的 CRLF 处理（issue #2880）：读取模板会为 LLM 把 CRLF 归一化为
    # LF，因此即使磁盘文件是 CRLF，old_string 也只会是 LF。依次尝试原样 old、
    # CRLF 变体、LF 变体。首次匹配会揭示该区域的换行风格；对 new 施加同样的
    # 变换，从而保留文件风格。
    old_crlf = old.replace('\\r\\n', '\\n').replace('\\n', '\\r\\n')
    old_lf = old.replace('\\r\\n', '\\n')
    new_crlf = new.replace('\\r\\n', '\\n').replace('\\n', '\\r\\n')
    new_lf = new.replace('\\r\\n', '\\n')
    count = 0
    matched_old, matched_new = old, new
    for cand_old, cand_new in ((old, new), (old_crlf, new_crlf), (old_lf, new_lf)):
        c = text.count(cand_old)
        if c >= 1:
            matched_old, matched_new, count = cand_old, cand_new, c
            break

    if count == 0:
        print(json.dumps({{'error': 'string_not_found'}}))
        sys.exit(0)
    if count > 1 and not replace_all:
        print(json.dumps({{'error': 'multiple_occurrences', 'count': count}}))
        sys.exit(0)

    result = text.replace(matched_old, matched_new) if replace_all else text.replace(matched_old, matched_new, 1)
    with open(path, 'wb') as f:
        f.write(result.encode('utf-8'))

    print(json.dumps({{'count': count}}))
except FileNotFoundError:
    print(json.dumps({{'error': 'file_not_found'}}))
except PermissionError:
    print(json.dumps({{'error': 'permission_denied'}}))
" 2>&1 <<'__ZHARNESS_EDIT_EOF__'
{payload_b64}
__ZHARNESS_EDIT_EOF__
"""
# Make sure to maintain a new line at the end of ZHARNESS_EDIT_EOF to denote end of
# feed. This may not matter for some integrations.
# 请务必在 ZHARNESS_EDIT_EOF 末尾保留一个换行以标识输入的结束。某些集成可能不在乎这一点。

"""Server-side file edit via `execute()`.

Reads the file, performs string replacement, and writes back — all on the
sandbox. The payload (path, old/new strings, `replace_all` flag) is passed as
base64-encoded JSON via heredoc stdin to avoid shell escaping issues.

Output: single-line JSON with `{{"count": N}}` on success or `{{"error": ...}}`
on failure.

Used for payloads under `_EDIT_INLINE_MAX_BYTES`; larger payloads fall back
to `_edit_via_upload()` which transfers old/new strings as temp files.

Keeps a trailing newline after `__ZHARNESS_EDIT_EOF__` so integrations that
detect end-of-input on a newline-delimited heredoc feed can observe completion.

通过 `execute()` 在服务端编辑文件。

读取文件、执行字符串替换并写回——全部在沙箱内完成。负载（path、old/new 字符串、
`replace_all` 标志）以 base64 编码的 JSON 经 heredoc stdin 传入，避免 shell 转义问题。

输出：成功时为单行 JSON `{{"count": N}}`，失败时为 `{{"error": ...}}`。

用于负载小于 `_EDIT_INLINE_MAX_BYTES` 的场景；更大的负载回退到
`_edit_via_upload()`，它将 old/new 字符串作为临时文件传输。

在 `__ZHARNESS_EDIT_EOF__` 之后保留一个尾部换行，使依赖换行定界的 heredoc
输入流来检测输入结束的集成能够观察到完成。
"""

_EDIT_INLINE_MAX_BYTES: Final = 50_000
"""Maximum combined byte size of `old_string` + `new_string` for inline server-side edit.

Payloads above this use _edit_via_upload (temp file upload + server-side replace)
to avoid size limits on the execute() request body imposed by some sandbox providers.

内联服务端编辑的 `old_string` + `new_string` 合并字节大小上限。

超过此大小的负载使用 _edit_via_upload（临时文件上传 + 服务端替换），
以规避部分沙箱提供商对 execute() 请求体的大小限制。
"""

_EDIT_TMPFILE_TEMPLATE = """python3 -c "
import os, stat as _stat, sys, json, base64

old_path = base64.b64decode('{old_path_b64}').decode('utf-8')
new_path = base64.b64decode('{new_path_b64}').decode('utf-8')
target = base64.b64decode('{target_b64}').decode('utf-8')
replace_all = {replace_all}

try:
    old = open(old_path, 'rb').read().decode('utf-8')
    new = open(new_path, 'rb').read().decode('utf-8')
except Exception as e:
    print(json.dumps({{'error': 'temp_read_failed', 'detail': str(e)}}))
    sys.exit(0)
finally:
    for p in (old_path, new_path):
        try: os.remove(p)
        except OSError: pass

try:
    st = os.stat(target)
    if not _stat.S_ISREG(st.st_mode):
        print(json.dumps({{'error': 'not_a_file'}}))
        sys.exit(0)

    with open(target, 'rb') as f:
        raw = f.read()

    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        print(json.dumps({{'error': 'not_a_text_file'}}))
        sys.exit(0)

    # Match-driven CRLF handling -- see _EDIT_COMMAND_TEMPLATE and issue #2880. / 基于匹配驱动的 CRLF 处理——参见 _EDIT_COMMAND_TEMPLATE 与 issue #2880。
    old_crlf = old.replace('\\r\\n', '\\n').replace('\\n', '\\r\\n')
    old_lf = old.replace('\\r\\n', '\\n')
    new_crlf = new.replace('\\r\\n', '\\n').replace('\\n', '\\r\\n')
    new_lf = new.replace('\\r\\n', '\\n')
    count = 0
    matched_old, matched_new = old, new
    for cand_old, cand_new in ((old, new), (old_crlf, new_crlf), (old_lf, new_lf)):
        c = text.count(cand_old)
        if c >= 1:
            matched_old, matched_new, count = cand_old, cand_new, c
            break

    if count == 0:
        print(json.dumps({{'error': 'string_not_found'}}))
        sys.exit(0)
    if count > 1 and not replace_all:
        print(json.dumps({{'error': 'multiple_occurrences', 'count': count}}))
        sys.exit(0)

    result = text.replace(matched_old, matched_new) if replace_all else text.replace(matched_old, matched_new, 1)
    with open(target, 'wb') as f:
        f.write(result.encode('utf-8'))

    print(json.dumps({{'count': count}}))
except FileNotFoundError:
    print(json.dumps({{'error': 'file_not_found'}}))
except PermissionError:
    print(json.dumps({{'error': 'permission_denied'}}))
" 2>&1"""
"""Server-side file edit via temp-file upload for large payloads.

Old/new strings are uploaded as temporary files via `upload_files()`, then this
script reads them, performs the replacement on the source file (which never
leaves the sandbox), and cleans up the temp files.

Output: single-line JSON with `{{"count": N}}` on success or
`{{"error": ...}}` on failure. Same success contract as
`_EDIT_COMMAND_TEMPLATE`; additionally produces
`{{"error": "temp_read_failed", "detail": ...}}` when the uploaded temp
files cannot be read.

针对大负载、通过临时文件上传在服务端编辑文件。

old/new 字符串通过 `upload_files()` 作为临时文件上传，随后本脚本读取它们、
在源文件上执行替换（源文件从不离开沙箱），并清理临时文件。

输出：成功时为单行 JSON `{{"count": N}}`，失败时为 `{{"error": ...}}`。
成功契约与 `_EDIT_COMMAND_TEMPLATE` 相同；当上传的临时文件无法读取时，
额外产生 `{{"error": "temp_read_failed", "detail": ...}}`。
"""

_READ_COMMAND_TEMPLATE = """python3 -c "
import codecs, os, stat as _stat, sys, base64, json

MAX_OUTPUT_BYTES = 500 * 1024
MAX_BINARY_BYTES = 500 * 1024
MAX_LINE_COUNT_BYTES = 1024 * 1024
TRUNCATION_MSG = '\\n\\n' + (
    '[Output was truncated due to size limits. '
    'This paginated read result exceeded the sandbox stdout limit. '
    'Continue reading with a larger offset or smaller limit to inspect the rest of the file.]'
)

path = base64.b64decode('{path_b64}').decode('utf-8')

try:
    st = os.stat(path)
    if not _stat.S_ISREG(st.st_mode):
        print(json.dumps({{'error': 'not_a_file'}}))
        sys.exit(0)

    if st.st_size == 0:
        print(json.dumps({{'encoding': 'utf-8', 'content': 'System reminder: File exists but has empty contents'}}))
        sys.exit(0)

    file_type = '{file_type}'
    if file_type != 'text':
        if st.st_size > MAX_BINARY_BYTES:
            print(json.dumps({{'error': 'Binary file exceeds maximum preview size of ' + str(MAX_BINARY_BYTES) + ' bytes'}}))
            sys.exit(0)
        with open(path, 'rb') as f:
            raw = f.read()
        print(json.dumps({{'encoding': 'base64', 'content': base64.b64encode(raw).decode('ascii')}}))
        sys.exit(0)

    with open(path, 'rb') as f:
        raw_prefix = f.read(8192)

    # The 8192-byte prefix can slice a multi-byte UTF-8 char (CJK is 3 bytes,
    # emoji is 4); the incremental decoder buffers a trailing partial sequence
    # instead of raising, so legitimate text isn't misclassified as binary.
    # 8192 字节的前缀可能切到多字节 UTF-8 字符（CJK 为 3 字节，emoji 为 4）；
    # 增量解码器会缓冲结尾的不完整序列而不是抛出异常，因此合法文本不会被
    # 误判为二进制。
    is_binary = False
    try:
        codecs.getincrementaldecoder('utf-8')().decode(raw_prefix, final=False)
    except UnicodeDecodeError:
        is_binary = True

    if is_binary:
        with open(path, 'rb') as f:
            raw = f.read()
        print(json.dumps({{'encoding': 'base64', 'content': base64.b64encode(raw).decode('ascii')}}))
        sys.exit(0)

    offset = {offset}
    limit = {limit}

    # No lines requested: no line range to report. Reached whenever a caller
    # asks for zero lines, including a negative limit that _build_read_cmd
    # floored to 0; without this the empty window would fall through to the
    # offset-exceeds-length error below. Checked here, after the not-found,
    # directory, empty-file, and binary branches, so real failures and the
    # empty-file reminder are still reported first.
    # 未请求任何行：无可报告的行范围。只要调用方请求零行即到达此处，包括
    # _build_read_cmd 将负数 limit 下取整到 0 的情况；否则空窗口会落入下方的
    # “偏移超过文件长度”错误。在此处、位于 not-found、目录、空文件与二进制
    # 分支之后检查，从而真实失败与空文件提示仍会被优先报告。
    if limit <= 0:
        print(json.dumps({{'encoding': 'utf-8', 'content': '', 'no_lines_requested': True}}))
        sys.exit(0)

    line_count = 0
    returned_lines = 0
    truncated = False
    parts = []
    current_bytes = 0
    msg_bytes = len(TRUNCATION_MSG.encode('utf-8'))
    effective_limit = MAX_OUTPUT_BYTES - msg_bytes

    at_eof = False
    with open(path, 'r', encoding='utf-8', newline=None) as f:
        while line_count < offset:
            raw_line = f.readline()
            if raw_line == '':
                at_eof = True
                break
            line_count += 1

        while not at_eof and returned_lines < limit and not truncated:
            raw_line = f.readline()
            if raw_line == '':
                at_eof = True
                break
            line_count += 1
            line = raw_line.rstrip('\\n').rstrip('\\r')
            piece = line if returned_lines == 0 else '\\n' + line
            piece_bytes = len(piece.encode('utf-8'))
            if current_bytes + piece_bytes > effective_limit:
                truncated = True
                remaining_bytes = effective_limit - current_bytes
                if remaining_bytes > 0:
                    prefix = piece.encode('utf-8')[:remaining_bytes].decode('utf-8', errors='ignore')
                    if prefix:
                        parts.append(prefix)
                        current_bytes += len(prefix.encode('utf-8'))
                break

            parts.append(piece)
            current_bytes += piece_bytes
            returned_lines += 1

        # The page can fill (returned_lines == limit) exactly at EOF without the
        # loop readline ever returning an empty string. Detect that via position:
        # after reading whole lines from a UTF-8 handle the decoder state is clean
        # at a line boundary, so tell() is the raw byte offset and equals st_size
        # at EOF. Worst case if this ever misjudges is a surfaced offset-exceeds-
        # length error on the next re-read (large files only, where total_lines
        # stays None) -- never a silent skip, since a false at_eof of True cannot
        # arise (a clean or packed tell() past EOF cannot equal st_size).
        # 页可能在恰好位于 EOF 处填满（returned_lines == limit）而循环 readline
        # 从不返回空字符串。通过位置检测：从 UTF-8 句柄整行读取后，解码器在行边界
        # 处状态干净，因此 tell() 是原始字节偏移，在 EOF 处等于 st_size。若此判断
        # 万一出错，最坏情况是下一次重读时暴露一个 offset 超过长度的错误（仅限
        # total_lines 保持 None 的大文件）——绝不会静默跳过，因为不可能产生错误的
        # at_eof 为 True（越过 EOF 的干净或紧凑 tell() 不可能等于 st_size）。
        if not at_eof:
            at_eof = f.tell() == st.st_size

    if returned_lines == 0 and not truncated:
        print(json.dumps({{'error': 'Line offset ' + str(offset) + ' exceeds file length (' + str(line_count) + ' lines)'}}))
        sys.exit(0)

    # When the page already reached EOF, reuse its scan's count for free.
    # Otherwise re-scan for the total only when the file is small enough that
    # the extra pass stays bounded; surrogateescape keeps an invalid byte after
    # the requested page from invalidating content that was decoded successfully.
    # 当页面已到达 EOF 时，直接复用本次扫描的计数。否则仅在文件足够小、
    # 额外扫描保持有界时才重新扫描以得到总行数；surrogateescape 使请求页之后
    # 的无效字节不会破坏已成功解码的内容。
    if at_eof:
        total_lines = line_count
    elif st.st_size <= MAX_LINE_COUNT_BYTES:
        with open(path, 'r', encoding='utf-8', errors='surrogateescape', newline=None) as f:
            total_lines = sum(1 for _ in f)
    else:
        total_lines = None

    text = ''.join(parts)
    if truncated:
        text += TRUNCATION_MSG

    # A byte cap can cut the final rendered line mid-way; that partial line is
    # deliberately not counted toward returned_lines (see the truncation
    # branch), so next_offset resumes at its start and the whole boundary line
    # is re-read. If even the first requested line overflows the cap no full
    # line was returned: advance by one so the read still makes progress instead
    # of looping on the same page (that line's tail is unreadable via line
    # offsets).
    # 字节上限可能在渲染的最后一行中间截断；该不完整行刻意不计入 returned_lines
    # （参见截断分支），从而 next_offset 从其开头续读，整条边界行会被重读。
    # 若连请求的第一行都超出上限，则没有返回任何完整行：将行数前进一，
    # 使读取仍能推进而非在同一页上循环（该行尾部无法通过行偏移读取）。
    if truncated and returned_lines == 0:
        returned_lines = 1

    end_line = offset + returned_lines
    if total_lines is not None:
        next_offset = end_line if end_line < total_lines else None
    else:
        # total_lines is None only via the large-file branch above, which is
        # reached only when the page stopped short of EOF, so lines always
        # remain here.
        # 只有经过上方的大文件分支 total_lines 才会为 None，而该分支仅在页面
        # 未到达 EOF 时才会被走到，因此此处始终还有行可读。
        next_offset = end_line
    print(json.dumps({{
        'encoding': 'utf-8',
        'content': text,
        'total_lines': total_lines,
        'start_line': offset + 1,
        'end_line': end_line,
        'next_offset': next_offset,
    }}))
except FileNotFoundError:
    print(json.dumps({{'error': 'file_not_found'}}))
except PermissionError:
    print(json.dumps({{'error': 'permission_denied'}}))
" 2>&1"""
"""Read file content with server-side pagination.

Runs on the sandbox via `execute()`. Only the requested page is returned,
avoiding full-file transfer for paginated text reads. The path is
base64-encoded; `file_type`, `offset`, and `limit` are interpolated directly.
`offset` and `limit` are model-supplied tool arguments, so interpolation is
only safe because `_build_read_cmd` coerces both to `int` via
`normalize_read_bounds` first — that coercion is what bounds them to integer
literals, and must not be removed.

Output: single-line JSON. On success (text): `{{"encoding", "content",
"total_lines", "start_line", "end_line", "next_offset"}}`, where `start_line`
and `end_line` are 1-indexed and `next_offset` is the 0-indexed offset of the
next unread line (`null` once the file is fully read). `total_lines` is `null`
when the file is large enough that a full re-scan to count its lines would be
unbounded. On success
(binary): `{{"encoding": "base64", "content": ...}}` without pagination keys.
An empty file short-circuits to `{{"encoding": "utf-8", "content": <empty-file
reminder>}}`, and a non-positive `limit` to `{{"encoding": "utf-8", "content":
"", "no_lines_requested": true}}`, both also without pagination keys. The
empty-file check runs first, so an empty file yields the reminder even when
`limit` is non-positive. On failure:
`{{"error": ...}}`.

在服务端分页读取文件内容。

通过 `execute()` 在沙箱上运行。只返回所请求的页，避免分页文本读取时的整文件传输。
路径经 base64 编码；`file_type`、`offset`、`limit` 直接插值。`offset` 与 `limit`
是模型提供的工具参数，插值之所以安全，仅因为 `_build_read_cmd` 先通过
`normalize_read_bounds` 将两者强制转为 `int`——正是该强制转换将它们限制为整数
字面量，绝不能移除。

输出：单行 JSON。成功（文本）时：`{{"encoding", "content", "total_lines",
"start_line", "end_line", "next_offset"}}`，其中 `start_line` 与 `end_line`
从 1 开始索引，`next_offset` 是下一条未读行的 0 基偏移（文件读完后为 `null`）。
当文件足够大、完整重扫来数行数会失去边界时，`total_lines` 为 `null`。成功
（二进制）时：`{{"encoding": "base64", "content": ...}}`，无分页键。空文件
短路为 `{{"encoding": "utf-8", "content": <空文件提示>}}`，非正 `limit`
短路为 `{{"encoding": "utf-8", "content": "", "no_lines_requested": true}}`，
两者同样没有分页键。空文件检查先执行，因此即使 `limit` 非正，空文件也会产生
提示。失败时：`{{"error": ...}}`。
"""


def _build_ls_cmd(path: str) -> str:
    path_b64 = base64.b64encode(path.encode("utf-8")).decode("ascii")
    return f"""python3 -c "
import os
import json
import base64

path = base64.b64decode('{path_b64}').decode('utf-8')

try:
    with os.scandir(path) as it:
        for entry in it:
            result = {{
                'path': os.path.join(path, entry.name),
                'is_dir': entry.is_dir(follow_symlinks=False)
            }}
            print(json.dumps(result))
except FileNotFoundError:
    print(json.dumps({{'error': 'path_not_found'}}))
except NotADirectoryError:
    print(json.dumps({{'error': 'not_a_directory'}}))
except PermissionError:
    print(json.dumps({{'error': 'permission_denied'}}))
" 2>/dev/null"""


def _parse_ls_output(output: str, path: str) -> LsResult:
    file_infos: list[FileInfo] = []
    error: str | None = None
    for line in output.strip().split("\n"):
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and "error" in data:
            error = data["error"]
            continue
        file_infos.append({"path": data["path"], "is_dir": data["is_dir"]})
    if error is not None:
        return LsResult(entries=None, error=f"Path '{path}': {error}")
    return LsResult(entries=file_infos)


def _build_read_cmd(file_path: str, offset: int, limit: int) -> str:
    file_type = _get_backend_read_file_type(file_path)
    path_b64 = base64.b64encode(file_path.encode("utf-8")).decode("ascii")
    # The `offset` clamp is load-bearing: the script has no negative-offset
    # guard of its own and would report `start_line` 0 or lower, which
    # `ReadResult` rejects. The `limit` clamp only normalizes negatives into the
    # zero-limit case that the script itself short-circuits. The `int()`
    # coercion inside the helper is what makes interpolating these
    # model-supplied values into the script source safe.
    # `offset` 的钳制是承重的：脚本自身没有负偏移防护，会报告 `start_line` 0 或
    # 更低，而 `ReadResult` 会拒绝它。`limit` 的钳制只是把负数归一化为脚本自身
    # 已短路处理的零 limit 情形。正是辅助函数内部的 `int()` 强转，才使得把这些
    # 模型提供的值插值进脚本源码变得安全。
    offset, limit = normalize_read_bounds(offset, limit)
    return _READ_COMMAND_TEMPLATE.format(
        path_b64=path_b64,
        file_type=file_type,
        offset=offset,
        limit=limit,
    )


def _parse_read_output(output: str, file_path: str) -> ReadResult:
    output = output.rstrip()
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        detail = output[:200] if output else "(empty)"
        return ReadResult(
            error=f"File '{file_path}': unexpected server response: {detail}"
        )
    if not isinstance(data, dict):
        detail = output[:200] if output else "(empty)"
        return ReadResult(
            error=f"File '{file_path}': unexpected server response: {detail}"
        )
    if "error" in data:
        return ReadResult(error=f"File '{file_path}': {data['error']}")
    # A parseable-but-malformed payload (missing `content`, or a pagination-key
    # combination `ReadResult.__post_init__` rejects) must degrade to the same
    # clean error result as a decode failure, not escape as a raw traceback.
    # 可解析但畸形的负载（缺少 `content`，或 `ReadResult.__post_init__` 拒绝的
    # 分页键组合）必须退化为与解码失败相同的干净错误结果，而不能逃逸为原始
    # traceback。
    try:
        return ReadResult(
            file_data=FileData(
                content=data["content"],
                encoding=data.get("encoding", "utf-8"),
            ),
            total_lines=data.get("total_lines"),
            start_line=data.get("start_line"),
            end_line=data.get("end_line"),
            next_offset=data.get("next_offset"),
            no_lines_requested=bool(data.get("no_lines_requested")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return ReadResult(
            error=f"File '{file_path}': unexpected server response: {exc}"
        )


def _build_write_preflight_cmd(file_path: str) -> str:
    path_b64 = base64.b64encode(file_path.encode("utf-8")).decode("ascii")
    return _WRITE_CHECK_TEMPLATE.format(path_b64=path_b64)


def _check_preflight_result(
    result: ExecuteResponse, file_path: str
) -> WriteResult | None:
    if result.exit_code != 0 or "Error:" in result.output:
        error_msg = result.output.strip() or f"Failed to write file '{file_path}'"
        return WriteResult(error=error_msg)
    return None


def _build_grep_cmd(
    pattern: str, path: str | None, glob: str | None, max_count: int | None = None
) -> str:
    search_path = shlex.quote(path or ".")
    # `-Z` separates the filename from line data with NUL, so filenames may
    # contain `:` without making the output ambiguous.
    # `-Z` 用 NUL 把文件名与行数据分隔开，因此文件名即使包含 `:` 也不会使输出
    # 产生歧义。
    grep_opts = "-rHnFZ"
    pattern_escaped = shlex.quote(pattern)

    # GNU `grep --include` only matches basenames, so a slash-containing glob
    # like `src/**/*.py` would silently match zero files. Route those to the
    # in-process Python template that resolves the glob relative to the search
    # root. Basename-only globs (no `/`) work correctly with `--include` and
    # are faster to run through GNU grep.
    # GNU `grep --include` 只匹配 basename，因此像 `src/**/*.py` 这样含斜杠的
    # glob 会静默匹配到零个文件。将此类模式路由到进程内 Python 模板，由它相对
    # 搜索根解析 glob。仅含 basename 的 glob（无 `/`）用 `--include` 即可正确
    # 工作，且经 GNU grep 运行更快。
    if glob and "/" in glob:
        path_b64 = base64.b64encode((path or ".").encode("utf-8")).decode("ascii")
        glob_b64 = base64.b64encode(glob.encode("utf-8")).decode("ascii")
        pattern_b64 = base64.b64encode(pattern.encode("utf-8")).decode("ascii")
        return _GREP_PATH_GLOB_TEMPLATE.format(
            path_b64=path_b64,
            glob_b64=glob_b64,
            pattern_b64=pattern_b64,
            max_count=None if max_count is None else int(max_count),
        )

    glob_pattern = f"--include={shlex.quote(glob)}" if glob else ""
    # Known limitation (pre-existing): `2>/dev/null` + `|| true` means a genuine
    # grep failure (exit 2 — unreadable root, bad option) is swallowed and parses
    # as an empty "no matches" result, indistinguishable from a real zero-match.
    # Surfacing exit 2 while still tolerating no-match (exit 1) AND the SIGPIPE
    # (exit 141) that `head` sends grep on the cap path requires `set -o pipefail`
    # (bash/zsh only, not POSIX sh/dash/busybox); buffering to a temp file instead
    # would defeat the `head` early-stop below. A portable fix belongs in its own
    # sandbox-tested change. The in-process `_GREP_PATH_GLOB_TEMPLATE` route does
    # surface its errors (see its docstring); only this GNU-grep route swallows.
    # 已知限制（既存）：`2>/dev/null` + `|| true` 意味着真正的 grep 失败
    # （exit 2——根不可读、选项错误）会被吞掉并解析为空的“无匹配”结果，
    # 与真实零匹配无法区分。既要呈现 exit 2 又要容忍无匹配（exit 1）以及上限路径上
    # `head` 发送给 grep 的 SIGPIPE（exit 141），需要 `set -o pipefail`
    # （仅 bash/zsh，不支持 POSIX sh/dash/busybox）；改用临时文件缓冲则会破坏下方
    # `head` 的提前停止。可移植的修复应作为独立的、经沙箱测试的改动。进程内
    # `_GREP_PATH_GLOB_TEMPLATE` 路径确实会呈现其错误（见其 docstring）；
    # 只有这条 GNU grep 路径会吞掉错误。
    base = f"grep {grep_opts} {glob_pattern} -e {pattern_escaped} {search_path} 2>/dev/null"
    if max_count is not None:
        # Read one record beyond the cap so the parser can distinguish "exactly
        # at the cap" (complete) from "capped early" (truncated). `head` closing
        # the pipe delivers SIGPIPE to grep, stopping it early rather than
        # letting it keep scanning a huge tree after the cap is met.
        # 多读一条超出上限的记录，使解析器能区分“恰在上限”（完整）与“提前截断”
        # （truncated）。`head` 关闭管道会向 grep 传递 SIGPIPE，使其在达到上限后
        # 提前停止，而不是继续扫描巨大的目录树。
        return f"{base} | head -n {int(max_count) + 1} || true"
    return f"{base} || true"


def _parse_grep_output(
    result: ExecuteResponse, path: str | None, max_count: int | None = None
) -> GrepResult:
    output = result.output.rstrip("\n")
    if result.exit_code is not None and result.exit_code != 0:
        detail = output.strip() if output else f"exit code {result.exit_code}"
        return GrepResult(error=f"Path '{path or '.'}': {detail}")
    if not output:
        return GrepResult(matches=[])
    matches: list[GrepMatch] = []
    parse_error: str | None = None
    for line in output.split("\n"):
        # Format is: path\0line_number:text / 格式为：path\0行号:文本
        try:
            file_path, rest = line.split("\0", 1)
            line_num_str, text = rest.split(":", 1)
            matches.append({"path": file_path, "line": int(line_num_str), "text": text})
        except ValueError:
            parse_error = line
    if parse_error is not None and not matches:
        return GrepResult(error=f"Path '{path or '.'}': {parse_error}")
    if max_count is not None and len(matches) > max_count:
        # More matches existed than the caller asked for; return the cap and
        # flag the result as incomplete.
        # 存在的匹配多于调用方要求的数量；返回上限内的结果并标记为不完整。
        return GrepResult(matches=matches[:max_count], truncated=True)
    return GrepResult(matches=matches)


def _build_glob_cmd(pattern: str, search_path: str) -> str:
    # Pass the user pattern through unchanged. The remote script walks with
    # `os.walk` (including hidden directories) and applies the shared basename /
    # path-relative contract (see template docstring).
    # 原样透传用户模式。远端脚本用 `os.walk`（包含隐藏目录）遍历，并应用共享的
    # basename/路径相对约定（参见模板 docstring）。
    pattern_b64 = base64.b64encode(pattern.encode("utf-8")).decode("ascii")
    path_b64 = base64.b64encode(search_path.encode("utf-8")).decode("ascii")
    return _GLOB_COMMAND_TEMPLATE.format(path_b64=path_b64, pattern_b64=pattern_b64)


def _glob_search_root(path: str | None) -> str:
    """Normalize a caller-supplied glob root to an absolute path.

    `_absolutize_glob_path` joins matches onto this root, so a relative root
    yields relative matches -- and `_check_fs_permission` only matches `deny`
    rules against absolute patterns, so those matches would bypass every rule.
    The middleware already forces a leading `/`, but `SandboxBackend.glob` is
    also called directly by SDK users.

    将调用方提供的 glob 根归一化为绝对路径。

    `_absolutize_glob_path` 会把匹配拼接到此根上，因此相对根会产生相对匹配——
    而 `_check_fs_permission` 只用绝对模式匹配 `deny` 规则，那些匹配会绕过所有
    规则。中间件已强制前导 `/`，但 SDK 用户也会直接调用 `SandboxBackend.glob`。
    """
    if not path:
        return "/"
    return path if path.startswith("/") else f"/{path}"


def _absolutize_glob_path(search_path: str, rel_path: str) -> str:
    """Join a search-root-relative glob match onto its search root.

    The remote script reports paths relative to the search root, but `glob`'s
    tool contract is absolute paths, and `_check_fs_permission` only matches
    absolute patterns — a relative path silently bypasses every `deny` rule.

    Args:
        search_path: Absolute search root the script was run against.
        rel_path: Path as reported by the script, relative unless already rooted.

    Returns:
        Absolute path for the match.

    把相对搜索根的 glob 匹配拼接到其搜索根上。

    远端脚本报告相对搜索根的路径，但 `glob` 的工具契约是绝对路径，
    且 `_check_fs_permission` 只匹配绝对模式——相对路径会静默绕过每条
    `deny` 规则。

    参数：
        search_path: 脚本所针对的绝对搜索根。
        rel_path: 脚本报告的路径，除非已带根否则为相对路径。

    返回：
        该匹配的绝对路径。
    """
    if rel_path.startswith("/"):
        return rel_path
    return f"{search_path.rstrip('/')}/{rel_path}"


_GlobLineKind = Literal["match", "error", "warning", "unparsed"]
"""Classification of one line of remote glob output. / 对远端 glob 输出的一行的分类。"""


def _classify_glob_line(line: str) -> tuple[_GlobLineKind, Any]:
    """Classify one line of remote glob output.

    Classification is total: every line lands in exactly one kind, with no
    "skip" outcome. That is what keeps a remote crash from being mistaken for a
    successful empty search -- see `_parse_glob_output`.

    Args:
        line: A single non-blank stdout line.

    Returns:
        `(kind, payload)` where `kind` is `"match"` (payload is the record, and
        its `"path"` is guaranteed to be a `str`), `"error"` (payload is the
        error code), `"warning"` (payload is the record) or `"unparsed"`
        (payload is the raw line).

    对远端 glob 输出的一行进行分类。

    分类是完备的：每一行都恰好落入一种类别，不存在“跳过”这一结果。
    正是这一点保证远端崩溃不会被误认为一次成功的空搜索——参见 `_parse_glob_output`。

    参数：
        line: 单个非空 stdout 行。

    返回：
        `(kind, payload)`，其中 `kind` 为 `"match"`（payload 是记录，其 `"path"`
        保证是 `str`）、`"error"`（payload 是错误码）、`"warning"`（payload 是
        记录）或 `"unparsed"`（payload 是原始行）。
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return ("unparsed", line)
    if not isinstance(data, dict):
        return ("unparsed", line)
    if "error" in data:
        return ("error", data["error"])
    if "warning" in data:
        return ("warning", data)
    # A non-`str` path would reach `_absolutize_glob_path` and raise at the tool
    # boundary; treat it as unparseable so it becomes a structured error instead.
    # 非 `str` 的路径会进入 `_absolutize_glob_path` 并在工具边界抛出异常；
    # 将其视为不可解析，从而变成结构化错误。
    if not isinstance(data.get("path"), str):
        return ("unparsed", line)
    return ("match", data)


def _glob_output_shortcut(
    result: ExecuteResponse, output: str, search_path: str
) -> GlobResult | None:
    """Resolve the two cases decidable without parsing any lines.

    Returns `None` when the output must still be parsed.

    A non-zero `exit_code` is a hard error: a killed or crashed helper otherwise
    reports "no files found" with full confidence, and the agent concludes the
    files do not exist. Empty output carries `result.truncated` through for the
    same reason -- a transport that clipped the output to nothing must not read
    as a confident empty result.

    解析无需处理任何行即可判定的两种情形。

    当输出仍需解析时返回 `None`。

    非零 `exit_code` 是硬错误：否则被杀或崩溃的辅助进程会满怀信心地报告
    “未找到文件”，agent 将据此断定文件不存在。空输出同理透传 `result.truncated`
    ——把输出截断成空的传输层绝不能被读作一次确信的空结果。
    """
    if result.exit_code is not None and result.exit_code != 0:
        detail = output[:200] if output else f"exit code {result.exit_code}"
        logger.error("Sandbox glob helper failed for path %r: %s", search_path, detail)
        return GlobResult(
            matches=None, error=f"Path '{search_path}': glob helper failed: {detail}"
        )
    if not output:
        return GlobResult(
            matches=[],
            truncated=result.truncated,
            truncation_reason="transport" if result.truncated else None,
        )
    return None


def _glob_warning_reason(
    payload: dict[str, Any],
    current: GlobTruncationReason | None,
    search_path: str,
) -> GlobTruncationReason | None:
    """Map one remote `warning` record to a truncation reason.

    Budget exhaustion and an unreadable subtree both mean "valid but partial",
    but they need opposite advice downstream: narrowing the search recovers
    budget-truncated matches and will never surface files under a directory we
    could not read. `unreadable` therefore outranks any reason already set.

    将一条远端 `warning` 记录映射为截断原因。

    预算耗尽与不可读子树都意味着“有效但不完整”，但下游需要相反的处置建议：
    收窄搜索能找回被预算截断的匹配，而永远无法呈现我们读不到的目录下的文件。
    因此 `unreadable` 的优先级高于任何已设置的原因。
    """
    if payload.get("warning") == "walk_errors":
        logger.warning(
            "Sandbox glob could not read %s path(s) under %r; results are incomplete. Sample: %s",
            payload.get("count", "an unknown number of"),
            search_path,
            payload.get("sample", []),
        )
        return "unreadable"
    if current is None:
        return "budget"
    return current


def _parse_glob_output(result: ExecuteResponse, search_path: str) -> GlobResult:
    """Parse the remote glob script's JSON-lines output into a `GlobResult`.

    Unrecognized lines are a hard error rather than a skip: with `2>&1` merging
    stderr into stdout, silently dropping them turns any remote crash into a
    successful empty search, and the agent concludes the files do not exist.
    The sole exception is an unparseable final line when *the transport* reports
    truncation, because that line may be an incomplete JSON record. The check
    reads `result.truncated` rather than the accumulated `truncated` below: a
    walk that self-reported a budget warning cannot produce a torn line, so
    letting a warning widen the exemption would swallow a real traceback.

    A non-zero `exit_code` is a hard error for the same reason -- a killed or
    crashed helper otherwise reports "no files found" with full confidence.

    Args:
        result: Raw `execute` response; its `truncated` flag means the transport
            clipped the output, so matches are incomplete.
        search_path: Search root, used to absolutize matches and prefix errors.

    Returns:
        `GlobResult` with absolute paths. `truncated` is `True` when the walk or
        the transport cut results short, with `truncation_reason` naming which.

    把远端 glob 脚本的 JSON 行输出解析为 `GlobResult`。

    无法识别的行是硬错误而非跳过：由于 `2>&1` 把 stderr 并入 stdout，
    静默丢弃它们会把任何远端崩溃变成一次成功的空搜索，agent 将据此断定文件
    不存在。唯一的例外是当*传输层*报告截断时那一条不可解析的末行，因为该行
    可能是不完整的 JSON 记录。此检查读取 `result.truncated` 而非下方累积的
    `truncated`：自报预算警告的遍历不可能产生撕裂的行，因此让警告扩大豁免范围
    会吞掉真正的 traceback。

    非零 `exit_code` 同样是硬错误——否则被杀或崩溃的辅助进程会满怀信心地报告
    “未找到文件”。

    参数：
        result: 原始 `execute` 响应；其 `truncated` 标志表示传输层截断了输出，
            因此匹配不完整。
        search_path: 搜索根，用于把匹配转为绝对路径并为错误加前缀。

    返回：
        带绝对路径的 `GlobResult`。当遍历或传输层截断了结果时 `truncated` 为
        `True`，`truncation_reason` 指明是哪一个。
    """
    output = result.output.strip()
    early = _glob_output_shortcut(result, output, search_path)
    if early is not None:
        return early
    file_infos: list[FileInfo] = []
    unparsed: list[str] = []
    error: str | None = None
    truncated = result.truncated
    reason: GlobTruncationReason | None = "transport" if result.truncated else None
    lines = output.split("\n")
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        kind, payload = _classify_glob_line(line)
        if kind == "match":
            file_infos.append(
                {
                    "path": _absolutize_glob_path(search_path, payload["path"]),
                    "is_dir": bool(payload.get("is_dir", False)),
                }
            )
        elif kind == "error":
            error = payload
        elif kind == "warning":
            truncated = True
            reason = _glob_warning_reason(payload, reason, search_path)
        elif not (result.truncated and index == len(lines) - 1):
            unparsed.append(payload)
        else:
            logger.debug(
                "Sandbox glob dropped a clipped final line for path %r: %s",
                search_path,
                payload[:200],
            )
    if error is not None:
        logger.error("Sandbox glob returned error %r for path %r", error, search_path)
        return GlobResult(matches=None, error=f"Path '{search_path}': {error}")
    if unparsed:
        logger.error(
            "Sandbox glob emitted %d unparseable line(s) for path %r; first: %s",
            len(unparsed),
            search_path,
            unparsed[0][:200],
        )
        return GlobResult(
            matches=None,
            error=f"Path '{search_path}': glob helper emitted unexpected output: {unparsed[0][:200]}",
        )
    return GlobResult(matches=file_infos, truncated=truncated, truncation_reason=reason)


def _build_edit_inline_cmd(
    file_path: str, old_string: str, new_string: str, *, replace_all: bool
) -> str:
    payload = json.dumps(
        {
            "path": file_path,
            "old": old_string,
            "new": new_string,
            "replace_all": replace_all,
        }
    )
    payload_b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return _EDIT_COMMAND_TEMPLATE.format(payload_b64=payload_b64)


def _map_edit_error(error: str, file_path: str, old_string: str) -> EditResult:
    """Map server-side error codes to `EditResult` objects. / 将服务端错误码映射为 `EditResult` 对象。"""
    messages: dict[str, str] = {
        "file_not_found": f"Error: File '{file_path}' not found",
        "permission_denied": f"Error: Permission denied editing file '{file_path}'",
        "not_a_file": f"Error: '{file_path}' is not a regular file",
        "not_a_text_file": f"Error: File '{file_path}' is not a text file",
        "string_not_found": f"Error: String not found in file: '{old_string}'",
        "multiple_occurrences": (
            f"Error: String '{old_string}' appears multiple times. Use replace_all=True to replace all occurrences."
        ),
    }
    return EditResult(
        error=messages.get(error, f"Error editing file '{file_path}': {error}")
    )


def _parse_edit_output(output: str, file_path: str, old_string: str) -> EditResult:
    output = output.rstrip()
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        detail = output[:200] if output else "(empty)"
        return EditResult(
            error=f"Error editing file '{file_path}': unexpected server response: {detail}"
        )
    if not isinstance(data, dict):
        detail = output[:200] if output else "(empty)"
        return EditResult(
            error=f"Error editing file '{file_path}': unexpected server response: {detail}"
        )
    if "error" in data:
        return _map_edit_error(data["error"], file_path, old_string)
    return EditResult(path=file_path, occurrences=data.get("count", 1))


def _build_edit_tmpfile_cmd(
    file_path: str, old_tmp: str, new_tmp: str, *, replace_all: bool
) -> str:
    return _EDIT_TMPFILE_TEMPLATE.format(
        old_path_b64=base64.b64encode(old_tmp.encode("utf-8")).decode("ascii"),
        new_path_b64=base64.b64encode(new_tmp.encode("utf-8")).decode("ascii"),
        target_b64=base64.b64encode(file_path.encode("utf-8")).decode("ascii"),
        replace_all=replace_all,
    )


_EXECUTE_CAPTURE_SENTINEL: Final = "__ZHARNESS_EXEC_META__"
"""First-line marker identifying capture-wrapper output: `<sentinel> <exit_code> <offloaded> <capped>`.

标识捕获包装器输出的首行标记：`<sentinel> <exit_code> <offloaded> <capped>`。
"""

_EXECUTE_CAPTURE_HEAD_LINES: Final = 5
_EXECUTE_CAPTURE_TAIL_LINES: Final = 5
_EXECUTE_CAPTURE_HEAD_BYTES: Final = 2000
_EXECUTE_CAPTURE_TAIL_BYTES: Final = 2000

_EXECUTE_CAPTURE_MAX_BYTES: Final = 10 * 1024 * 1024
"""Hard cap on captured stdout/stderr persisted to the sandbox.

Bounds sandbox disk use for runaway output: the captured stream is piped through
`head -c`, so when the cap is hit the writer receives `SIGPIPE` and nothing
further reaches disk even if the command ignores the signal. Set well above the
inline budget so legitimately large output is still preserved in full; output
beyond the cap is truncated and flagged.

持久化到沙箱的捕获 stdout/stderr 的硬上限。

限制失控输出对沙箱磁盘的占用：捕获流经 `head -c` 管道，因此达到上限时写入方会
收到 `SIGPIPE`，即使命令忽略该信号，后续内容也不会再写入磁盘。取值远高于内联
预算，使合法的大输出仍能完整保留；超过上限的输出会被截断并标记。
"""

# The captured stream is piped into `head -c` (caps the on-disk file) followed by
# `cat > /dev/null` (drains the rest), so the file can never exceed the cap yet the
# command still reaches EOF and exits normally -- closing the pipe early would
# SIGPIPE-kill it and corrupt its exit code. Because the command is in a pipeline,
# its real exit code is recovered from a sidecar file rather than `$?` (which would
# be the pipeline's). The command runs in a subshell so a command `exit` cannot
# abort the wrapper, and `eval` preserves the backend's own shell/env. The command
# is embedded via a quoted heredoc with a random delimiter to avoid shell-quoting
# issues; the (internal, sanitized) path is shell-quoted.
# 捕获流先经 `head -c` 管道（限制磁盘文件大小），再经 `cat > /dev/null`
# （排空其余内容），因此文件绝不会超过上限，而命令仍能到达 EOF 并正常退出——
# 提前关闭管道会用 SIGPIPE 杀死命令并破坏其退出码。由于命令位于管道中，
# 其真实退出码从旁车文件恢复，而非 `$?`（那将是管道的退出码）。命令在子
# shell 中运行，故命令自身的 `exit` 无法中止包装器；`eval` 保留后端自身的
# shell/环境。命令通过带随机分隔符的引号 heredoc 嵌入，以避免 shell 引号问题；
# 该（内部、已清洗的）路径做了 shell 引号处理。
_EXECUTE_CAPTURE_CMD_TEMPLATE = """# ===== zharness capture-at-source offload (auto-generated wrapper) ===== 源头捕获执行输出的自动生成包装器
# Runs the requested command below, capturing its combined output to a file in
# the sandbox: returned inline when small, or as a head/tail preview when large
# (the full result stays at the path for read_file). Disable this wrapping with
# BaseSandbox.enable_capture_offload = False.
# 运行下方请求的命令，将其合并输出捕获到沙箱中的一个文件：较小时内联返回，
# 较大时返回头/尾预览（完整结果保留在该路径供 read_file 使用）。通过设置
# BaseSandbox.enable_capture_offload = False 可关闭此包装。
__da_f=__PATH_Q__
__da_ecf="$__da_f.ec"
mkdir -p "$(dirname "$__da_f")" 2>/dev/null
# ----- requested command (verbatim, between the heredoc markers) ----- 请求的命令（原样，位于 heredoc 标记之间）
__da_cmd=$(cat <<'__DELIM__'
__COMMAND__
__DELIM__
)
# ----- end requested command; everything below is offload machinery ----- 请求的命令到此结束；下方全部是卸载（offload）机制
{ ( eval "$__da_cmd" ); echo "$?" > "$__da_ecf"; } 2>&1 | { head -c __MAXBYTES__ > "$__da_f"; cat > /dev/null; }
__da_ec=$(cat "$__da_ecf" 2>/dev/null)
: "${__da_ec:=1}"
rm -f "$__da_ecf"
__da_bytes=$(wc -c < "$__da_f" 2>/dev/null | tr -d ' ')
: "${__da_bytes:=0}"
__da_capped=0
[ "$__da_bytes" -ge __MAXBYTES__ ] && __da_capped=1
if [ "$__da_bytes" -le __BUDGET__ ]; then
  printf '%s %s %s %s\\n' '__SENTINEL__' "$__da_ec" 0 0
  cat "$__da_f"
  rm -f "$__da_f"
else
  __da_lines=$(wc -l < "$__da_f" 2>/dev/null | tr -d ' ')
  : "${__da_lines:=0}"
  __da_omitted=$((__da_lines - __HEADLINES__ - __TAILLINES__))
  printf '%s %s %s %s\\n' '__SENTINEL__' "$__da_ec" 1 "$__da_capped"
  if [ "$__da_omitted" -gt 0 ]; then
    head -c __HEAD__ "$__da_f" | head -n __HEADLINES__
    printf '... [%s lines truncated] ...\\n' "$__da_omitted"
    tail -c __TAIL__ "$__da_f" | tail -n __TAILLINES__
  else
    head -c $((__HEAD__ + __TAIL__)) "$__da_f"
  fi
fi
"""
# Pure POSIX sh wrapper for capture-at-source `execute`; see the comment above the template. / 用于源头捕获 `execute` 的纯 POSIX sh 包装器；参见模板上方的注释。


def _new_heredoc_delim() -> str:
    """Return a random heredoc delimiter, e.g. `__ZHARNESS_CMD_<80 random bits>__`.

    返回一个随机 heredoc 分隔符，例如 `__ZHARNESS_CMD_<80 位随机位>__`。
    """
    return (
        "__ZHARNESS_CMD_"
        + base64.b32encode(os.urandom(10)).decode("ascii").rstrip("=")
        + "__"
    )


def _build_capture_execute_cmd(
    command: str,
    capture_path: str,
    *,
    inline_budget: int,
    max_capture_bytes: int | None = None,
) -> str:
    """Build the capture-at-source wrapper command for `execute`.

    `inline_budget` is the byte threshold at or below which output is returned
    inline; above it the output is left at `capture_path` and only a head/tail
    preview is returned. Captured output is hard-capped at `max_capture_bytes`
    (defaulting to `_EXECUTE_CAPTURE_MAX_BYTES`, resolved here so it stays
    overridable/patchable); beyond that it is truncated and flagged.

    为 `execute` 构建源头捕获包装器命令。

    `inline_budget` 是字节阈值，输出等于或低于它时内联返回；高于它时输出保留在
    `capture_path`，只返回头/尾预览。捕获输出在 `max_capture_bytes` 处硬上限
    （默认为 `_EXECUTE_CAPTURE_MAX_BYTES`，在此解析以保持可覆盖/可修补）；
    超过上限则被截断并标记。
    """
    cap = (
        max_capture_bytes
        if max_capture_bytes is not None
        else _EXECUTE_CAPTURE_MAX_BYTES
    )
    # The command is embedded in a quoted heredoc; guarantee the delimiter cannot
    # appear in it so the command can never terminate the heredoc early. The
    # delimiter is 80 random bits, so this regenerates only astronomically rarely.
    # 命令嵌入在引号 heredoc 中；保证分隔符不会出现在命令里，从而命令永远无法
    # 提前终止 heredoc。分隔符为 80 位随机位，因此几乎从不需重新生成。
    delim = _new_heredoc_delim()
    while delim in command:
        delim = _new_heredoc_delim()
    # __COMMAND__ is substituted last so command content can never collide with a
    # remaining placeholder token.
    # __COMMAND__ 最后才被替换，因此命令内容永远无法与剩余的占位符 token 冲突。
    return (
        _EXECUTE_CAPTURE_CMD_TEMPLATE.replace("__PATH_Q__", shlex.quote(capture_path))
        .replace("__DELIM__", delim)
        .replace("__MAXBYTES__", str(cap))
        .replace("__BUDGET__", str(inline_budget))
        .replace("__SENTINEL__", _EXECUTE_CAPTURE_SENTINEL)
        .replace("__HEADLINES__", str(_EXECUTE_CAPTURE_HEAD_LINES))
        .replace("__TAILLINES__", str(_EXECUTE_CAPTURE_TAIL_LINES))
        .replace("__HEAD__", str(_EXECUTE_CAPTURE_HEAD_BYTES))
        .replace("__TAIL__", str(_EXECUTE_CAPTURE_TAIL_BYTES))
        .replace("__COMMAND__", command)
    )


def _parse_capture_execute_output(
    output: str, *, backend_truncated: bool = False
) -> ExecuteOffloadResult:
    r"""Parse capture-wrapper stdout into an `ExecuteOffloadResult`.

    The wrapper emits a meta line followed by the body:

        <sentinel> <exit_code> <offloaded> <capped>\n<inline output or preview>

    i.e. four space-separated fields on the first line — the sentinel, the
    command's exit code, `1`/`0` for whether output was offloaded to the capture
    file, and `1`/`0` for whether it hit the size cap — then everything after the
    first newline is the body (full output when inline, head/tail preview when
    offloaded).

    Falls back to `offloaded=False` with the raw output when the meta line is
    absent or malformed — e.g. if the backend truncated transport; the caller
    must not re-run the command in that case. `response.truncated` is set when the
    captured output hit the size cap (the saved file is incomplete) or
    `backend_truncated` is passed through from the underlying `execute`.

    将捕获包装器的 stdout 解析为 `ExecuteOffloadResult`。

    包装器先输出一行元信息，后跟正文：

        <sentinel> <exit_code> <offloaded> <capped>\n<内联输出或预览>

    即首行为四个空格分隔的字段——sentinel、命令退出码、输出是否卸载到捕获文件的
    `1`/`0`、是否达到大小上限的 `1`/`0`——首个换行之后全部是正文（内联时为完整
    输出，卸载时为头/尾预览）。

    当元信息行缺失或畸形时——例如后端截断了传输——回退为携带原始输出的
    `offloaded=False`；此时调用方不得重新运行该命令。当捕获输出达到大小上限
    （保存的文件不完整）或从底层 `execute` 透传 `backend_truncated` 时，
    设置 `response.truncated`。
    """
    first, _, body = output.partition("\n")
    parts = first.split(" ")
    # Expect exactly the four meta fields described above; anything else is not
    # our wrapper's output, so fall back to returning it verbatim.
    # 期望恰好为上述四个元字段；任何其他内容都不是我们包装器的输出，
    # 因此回退为原样返回。
    if len(parts) != 4 or parts[0] != _EXECUTE_CAPTURE_SENTINEL:
        return ExecuteOffloadResult(
            offloaded=False,
            response=ExecuteResponse(output=output, truncated=backend_truncated),
        )
    try:
        exit_code = int(parts[1])
    except ValueError:
        return ExecuteOffloadResult(
            offloaded=False,
            response=ExecuteResponse(output=output, truncated=backend_truncated),
        )
    return ExecuteOffloadResult(
        offloaded=parts[2] == "1",
        response=ExecuteResponse(
            output=body,
            exit_code=exit_code,
            truncated=parts[3] == "1" or backend_truncated,
        ),
    )


class BaseSandbox(SandboxBackendProtocol, ABC):
    """Base sandbox implementation with `execute()` as the core abstract method.

    This class provides default implementations for all protocol methods.
    File listing, grep, and glob use shell commands via `execute()`. Read uses
    a server-side Python script via `execute()` for paginated access. Write
    delegates content transfer to `upload_files()`. Edit uses a server-side
    script for small payloads and uploads old/new strings as temp files with
    a server-side replace for large ones.

    !!! note

        `BaseSandbox` does not reduce or partition the trust boundary of
        `execute()`. Its helper methods are convenience wrappers built on top of
        the subclass-provided command-execution primitive and assume callers who
        can use `BaseSandbox` already have whatever shell-execution capability
        that backend exposes.

    Subclasses must implement `execute()`, `upload_files()`, `download_files()`,
    and the `id` property.

    以 `execute()` 为核心抽象方法的沙箱基类实现。

    本类为所有协议方法提供默认实现。文件列举、grep 与 glob 通过 `execute()` 使用
    shell 命令。读取通过 `execute()` 运行服务端 Python 脚本以实现分页访问。写入把
    内容传输委托给 `upload_files()`。编辑对小负载使用服务端脚本，对大负载把
    old/new 字符串作为临时文件上传并在服务端替换。

    !!! note

        `BaseSandbox` 不缩小也不分割 `execute()` 的信任边界。其辅助方法只是建立在
        子类提供的命令执行原语之上的便捷包装，并假定能够使用 `BaseSandbox` 的调用方
        已经拥有该后端暴露的任意 shell 执行能力。

    子类必须实现 `execute()`、`upload_files()`、`download_files()` 以及
    `id` 属性。
    """

    enable_capture_offload: bool = False
    """Whether `FilesystemMiddleware` may use capture-at-source offload for `execute`.

    When `True`, large `execute` output is captured to a file in the sandbox and
    only a preview is returned, avoiding a round-trip back through the agent
    process. Defaults to `False` (opt-in) because the capture wrapper's shell and
    coreutils assumptions are not guaranteed on every sandbox image; subclasses
    known to be compatible set it to `True`. When `False`, `execute_with_offload`
    runs the command unwrapped and the middleware falls back to inline execution
    plus generic eviction.

    `FilesystemMiddleware` 是否可为 `execute` 使用源头捕获卸载（capture-at-source offload）。

    为 `True` 时，较大的 `execute` 输出被捕获到沙箱内的文件中，只返回预览，
    避免经过 agent 进程的往返。默认为 `False`（需显式启用），因为捕获包装器的
    shell 与 coreutils 假设并非在每个沙箱镜像上都成立；已知兼容的子类会将其设为
    `True`。为 `False` 时，`execute_with_offload` 不加包装地运行命令，中间件回退为
    内联执行加上通用驱逐。
    """

    @abstractmethod
    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Execute a command in the sandbox and return `ExecuteResponse`.

        Args:
            command: Full shell command string to execute.
            timeout: Maximum time in seconds to wait for the command to complete.

                If `None`, uses the backend's default timeout.

        Returns:
            `ExecuteResponse` with combined output, exit code, and truncation flag.

        在沙箱中执行命令并返回 `ExecuteResponse`。

        参数：
            command: 要执行的完整 shell 命令字符串。
            timeout: 等待命令完成的最大秒数。

                若为 `None`，使用后端的默认超时。

        返回：
            包含合并输出、退出码与截断标志的 `ExecuteResponse`。
        """

    def execute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int | None = None,
    ) -> ExecuteOffloadResult:
        """Run `command`, offloading large output to a file in the sandbox.

        Captures the command's combined output: returned inline when it is at or
        below `max_inline_bytes`, otherwise left at `capture_path` (so the caller
        can surface a `read_file` pointer) with only a head/tail preview returned.
        Captured output is hard-capped at `max_capture_bytes` (default
        `_EXECUTE_CAPTURE_MAX_BYTES`) without killing the command, so the exit
        code is preserved. When `enable_capture_offload` is `False`, the command
        runs unwrapped and the full output is returned (`offloaded=False`), so
        callers can fall back to their own handling (e.g. generic eviction).

        Returns:
            An `ExecuteOffloadResult`. `offloaded=True` when the result was left
            at `capture_path` and `response.output` holds only the preview;
            `offloaded=False` when `response.output` is the complete output.

        运行 `command`，将较大输出卸载到沙箱内的文件中。

        捕获命令的合并输出：等于或低于 `max_inline_bytes` 时内联返回，否则保留在
        `capture_path`（调用方可据此给出 `read_file` 指针）且只返回头/尾预览。
        捕获输出在 `max_capture_bytes`（默认 `_EXECUTE_CAPTURE_MAX_BYTES`）处硬
        上限且不杀死命令，从而保留退出码。当 `enable_capture_offload` 为 `False`
        时，命令不加包装地运行并返回完整输出（`offloaded=False`），调用方可以
        回退到自己的处理方式（例如通用驱逐）。

        返回：
            一个 `ExecuteOffloadResult`。当结果保留在 `capture_path` 且
            `response.output` 仅含预览时 `offloaded=True`；当 `response.output`
            是完整输出时 `offloaded=False`。
        """
        use_timeout = timeout is not None and execute_accepts_timeout(type(self))
        if not self.enable_capture_offload:
            result = (
                self.execute(command, timeout=timeout)
                if use_timeout
                else self.execute(command)
            )
            return ExecuteOffloadResult(offloaded=False, response=result)
        wrapper = _build_capture_execute_cmd(
            command,
            capture_path,
            inline_budget=max_inline_bytes,
            max_capture_bytes=max_capture_bytes,
        )
        result = (
            self.execute(wrapper, timeout=timeout)
            if use_timeout
            else self.execute(wrapper)
        )
        return _parse_capture_execute_output(
            result.output, backend_truncated=result.truncated
        )

    async def aexecute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int
        | None = None,  # forwarded to the backend, not an asyncio timeout / 转发给后端，而非 asyncio 超时
    ) -> ExecuteOffloadResult:
        """Async version of `execute_with_offload`, delegating to `aexecute`. / `execute_with_offload` 的异步版本，委托给 `aexecute`。"""
        use_timeout = timeout is not None and execute_accepts_timeout(type(self))
        if not self.enable_capture_offload:
            result = (
                await self.aexecute(command, timeout=timeout)
                if use_timeout
                else await self.aexecute(command)
            )
            return ExecuteOffloadResult(offloaded=False, response=result)
        wrapper = _build_capture_execute_cmd(
            command,
            capture_path,
            inline_budget=max_inline_bytes,
            max_capture_bytes=max_capture_bytes,
        )
        result = (
            await self.aexecute(wrapper, timeout=timeout)
            if use_timeout
            else await self.aexecute(wrapper)
        )
        return _parse_capture_execute_output(
            result.output, backend_truncated=result.truncated
        )

    def ls(self, path: str) -> LsResult:
        """Structured listing with file metadata using os.scandir. / 使用 os.scandir 进行的结构化列举，附带文件元数据。"""
        result = self.execute(_build_ls_cmd(path))
        return _parse_ls_output(result.output, path)

    async def als(self, path: str) -> LsResult:
        """Async version of `ls`, delegating to `aexecute`. / `ls` 的异步版本，委托给 `aexecute`。"""
        result = await self.aexecute(_build_ls_cmd(path))
        return _parse_ls_output(result.output, path)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Read file content with server-side line-based pagination.

        Runs a Python script on the sandbox via `execute()` that reads the
        file, detects encoding, and applies offset/limit pagination for text
        files. Only the requested page is returned over the wire, and text
        output is capped to about 500 KiB to avoid backend stdout/log transport
        failures. When that cap is exceeded, the returned content is truncated
        with guidance to continue pagination using a different `offset` or
        smaller `limit`.

        Binary files (non-UTF-8) are returned base64-encoded without
        pagination.

        Args:
            file_path: Absolute path to the file to read.
            offset: Starting line number (0-indexed).

                Only applied to text files, and clamped to the start of the file
                when negative.
            limit: Maximum number of lines to return.

                Only applied to text files with content: a non-positive value
                returns empty content with no pagination metadata. Empty files
                return the empty-file reminder regardless of `limit`.

        Returns:
            `ReadResult` with `file_data` on success or `error` on failure.

        在服务端按行分页读取文件内容。

        通过 `execute()` 在沙箱上运行一个 Python 脚本，读取文件、检测编码，并对
        文本文件应用 offset/limit 分页。只通过网络返回所请求的页，文本输出上限约
        500 KiB，以避免后端 stdout/日志传输失败。超过该上限时，返回内容被截断，
        并给出使用不同 `offset` 或更小 `limit` 继续分页的指引。

        二进制文件（非 UTF-8）以 base64 编码返回，不做分页。

        参数：
            file_path: 要读取文件的绝对路径。
            offset: 起始行号（0 基）。

                仅应用于文本文件，为负时钳制到文件开头。
            limit: 要返回的最大行数。

                仅应用于有内容的文本文件：非正值返回空内容且无分页元数据。
                空文件无论 `limit` 为何都返回空文件提示。

        返回：
            成功时带 `file_data` 的 `ReadResult`，失败时带 `error`。
        """
        result = self.execute(_build_read_cmd(file_path, offset, limit))
        return _parse_read_output(result.output, file_path)

    async def aread(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        """Async version of `read`, delegating to `aexecute`. / `read` 的异步版本，委托给 `aexecute`。"""
        result = await self.aexecute(_build_read_cmd(file_path, offset, limit))
        return _parse_read_output(result.output, file_path)

    def _write_preflight(self, file_path: str) -> WriteResult | None:
        """Create parent directories for `write()`.

        Subclasses overriding `write()` (e.g., to use a native SDK transport)
        should call this first so they preserve the parent-mkdir semantics of
        `BaseSandbox.write()`. There is a TOCTOU window between this and the
        actual write — an inherent limitation of splitting the operation across
        two backend calls.

        Args:
            file_path: Absolute path for the file about to be written.

        Returns:
            `None` if the preflight passes (parents created); a populated
                `WriteResult` with `error` set if the preflight fails.

        为 `write()` 创建父目录。

        覆写 `write()` 的子类（例如为使用原生 SDK 传输）应首先调用此方法，
        以保留 `BaseSandbox.write()` 的父目录 mkdir 语义。此方法与实际写入之间存在
        一个 TOCTOU 窗口——这是把操作拆分为两次后端调用的固有局限。

        参数：
            file_path: 即将写入文件的绝对路径。

        返回：
            预检通过（父目录已创建）时为 `None`；预检失败时为设置了 `error` 的
            `WriteResult`。
        """
        result = self.execute(_build_write_preflight_cmd(file_path))
        return _check_preflight_result(result, file_path)

    async def _awrite_preflight(self, file_path: str) -> WriteResult | None:
        """Async version of `_write_preflight`, delegating to `aexecute`. / `_write_preflight` 的异步版本，委托给 `aexecute`。"""
        result = await self.aexecute(_build_write_preflight_cmd(file_path))
        return _check_preflight_result(result, file_path)

    def write(
        self,
        file_path: str,
        content: str,
    ) -> WriteResult:
        """Write content to a file, creating or overwriting it if it already exists.

        Args:
            file_path: Absolute path for the file.
            content: UTF-8 text content to write.

        Returns:
            `WriteResult` with `path` on success or `error` on failure.

        把内容写入文件，若文件已存在则创建或覆盖之。

        参数：
            file_path: 文件的绝对路径。
            content: 要写入的 UTF-8 文本内容。

        返回：
            成功时带 `path` 的 `WriteResult`，失败时带 `error`。
        """
        preflight_error = self._write_preflight(file_path)
        if preflight_error is not None:
            return preflight_error

        responses = self.upload_files([(file_path, content.encode("utf-8"))])
        if not responses:
            # An unreachable condition was reached / 到达了一个不可达的条件
            msg = f"Responses was expected to return 1 result, but it returned {len(responses)} with type {type(responses)}"
            raise AssertionError(msg)
        response = responses[0]
        if response.error:
            return WriteResult(
                error=f"Failed to write file '{file_path}': {response.error}"
            )

        return WriteResult(path=file_path)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """Async version of `write`, delegating to `aexecute` and `aupload_files`. / `write` 的异步版本，委托给 `aexecute` 与 `aupload_files`。"""
        preflight_error = await self._awrite_preflight(file_path)
        if preflight_error is not None:
            return preflight_error
        responses = await self.aupload_files([(file_path, content.encode("utf-8"))])
        if not responses:
            msg = f"Responses was expected to return 1 result, but it returned {len(responses)} with type {type(responses)}"
            raise AssertionError(msg)
        response = responses[0]
        if response.error:
            return WriteResult(
                error=f"Failed to write file '{file_path}': {response.error}"
            )
        return WriteResult(path=file_path)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Edit a file by replacing exact string occurrences.

        For small payloads (combined old/new under `_EDIT_INLINE_MAX_BYTES`),
        runs a server-side Python script via `execute()` — single round-trip,
        no file transfer.  For larger payloads, uploads old/new strings as
        temp files and runs a server-side replace script — the source file
        never leaves the sandbox.

        `read()` normalizes CRLF to LF for the LLM, so `old_string` is
        typically LF-only. The server-side script tries `old_string` as-is
        first, then CRLF- and LF-normalized variants, and applies the same
        transform to `new_string` so the file's line-ending style is
        preserved on write. On mixed-ending files, `replace_all=True` only
        touches occurrences in the first matching style — subsequent edits
        can replace the rest.

        Args:
            file_path: Absolute path to the file to edit.
            old_string: The exact substring to find.
            new_string: The replacement string.
            replace_all: If `True`, replace every occurrence.

                If `False` (default), error when more than one
                occurrence exists.

        Returns:
            `EditResult` with `path` and `occurrences` on success, or `error`
                on failure.

        通过替换精确字符串出现来编辑文件。

        对于小负载（合并的 old/new 小于 `_EDIT_INLINE_MAX_BYTES`），通过
        `execute()` 运行服务端 Python 脚本——单次往返、无文件传输。对于更大的
        负载，把 old/new 字符串作为临时文件上传并运行服务端替换脚本——源文件
        从不离开沙箱。

        `read()` 会为 LLM 把 CRLF 归一化为 LF，因此 `old_string` 通常只有 LF。
        服务端脚本先尝试原样的 `old_string`，再尝试 CRLF 与 LF 归一化变体，
        并对 `new_string` 施加同样的变换，使文件在写入时保留换行风格。在混合换行
        文件上，`replace_all=True` 只处理第一种匹配风格的出现——后续编辑可替换其余部分。

        参数：
            file_path: 要编辑文件的绝对路径。
            old_string: 要查找的精确子串。
            new_string: 替换字符串。
            replace_all: 若为 `True`，替换每一次出现。

                若为 `False`（默认），当出现多次时报错。

        返回：
            成功时带 `path` 与 `occurrences` 的 `EditResult`，失败时带 `error`。
        """
        payload_size = len(old_string.encode("utf-8")) + len(new_string.encode("utf-8"))

        if payload_size <= _EDIT_INLINE_MAX_BYTES:
            return self._edit_inline(file_path, old_string, new_string, replace_all)

        return self._edit_via_upload(file_path, old_string, new_string, replace_all)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """Async version of `edit`, delegating to `aexecute` and `aupload_files`. / `edit` 的异步版本，委托给 `aexecute` 与 `aupload_files`。"""
        payload_size = len(old_string.encode("utf-8")) + len(new_string.encode("utf-8"))
        if payload_size <= _EDIT_INLINE_MAX_BYTES:
            return await self._aedit_inline(
                file_path, old_string, new_string, replace_all
            )
        return await self._aedit_via_upload(
            file_path, old_string, new_string, replace_all
        )

    def _edit_inline(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool,
    ) -> EditResult:
        """Server-side replace via `execute()` (single round-trip). / 通过 `execute()` 在服务端替换（单次往返）。"""
        result = self.execute(
            _build_edit_inline_cmd(
                file_path, old_string, new_string, replace_all=replace_all
            )
        )
        return _parse_edit_output(result.output, file_path, old_string)

    async def _aedit_inline(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool,
    ) -> EditResult:
        """Async version of `_edit_inline`, delegating to `aexecute`. / `_edit_inline` 的异步版本，委托给 `aexecute`。"""
        result = await self.aexecute(
            _build_edit_inline_cmd(
                file_path, old_string, new_string, replace_all=replace_all
            )
        )
        return _parse_edit_output(result.output, file_path, old_string)

    def _edit_via_upload(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool,
    ) -> EditResult:
        """Upload old/new as temp files, replace server-side.

        The source file never leaves the sandbox. Only the old/new strings are
        transferred via `upload_files()`, and a server-side script reads them,
        performs the replacement, and cleans up the temp files.

        把 old/new 作为临时文件上传，在服务端替换。

        源文件从不离开沙箱。只有 old/new 字符串经 `upload_files()` 传输，
        由服务端脚本读取它们、执行替换并清理临时文件。
        """
        uid = base64.b32encode(os.urandom(10)).decode("ascii").lower()
        old_tmp = f"/tmp/.zharness_edit_{uid}_old"  # sandbox-internal temp file with 80-bit random uid / 沙箱内部临时文件，带 80 位随机 uid
        new_tmp = f"/tmp/.zharness_edit_{uid}_new"

        resps = self.upload_files(
            [
                (old_tmp, old_string.encode("utf-8")),
                (new_tmp, new_string.encode("utf-8")),
            ]
        )
        if len(resps) < 2:  # expecting exactly 2 responses
            return EditResult(
                error=f"Error editing file '{file_path}': upload returned no response"
            )
        for r in resps:
            if r.error:
                return EditResult(error=f"Error editing file '{file_path}': {r.error}")

        cmd = _build_edit_tmpfile_cmd(
            file_path, old_tmp, new_tmp, replace_all=replace_all
        )
        result = self.execute(cmd)
        output = result.output.rstrip()

        try:
            data = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            # Script may not have started or its finally block may not have
            # run — best-effort cleanup of temp files.
            # 脚本可能尚未启动，或其 finally 块可能未执行——尽力清理临时文件。
            cleanup = self.execute(
                f"rm -f {shlex.quote(old_tmp)} {shlex.quote(new_tmp)}"
            )
            if cleanup.exit_code != 0:
                logger.warning(
                    "Failed to clean up temp files for edit %s: %s",
                    file_path,
                    cleanup.output[:200],
                )
            detail = output[:200] if output else "(empty)"
            return EditResult(
                error=f"Error editing file '{file_path}': unexpected server response: {detail}"
            )

        if not isinstance(data, dict):
            detail = output[:200] if output else "(empty)"
            return EditResult(
                error=f"Error editing file '{file_path}': unexpected server response: {detail}"
            )

        if "error" in data:
            return _map_edit_error(data["error"], file_path, old_string)

        return EditResult(
            path=file_path,
            occurrences=data.get("count", 1),
        )

    async def _aedit_via_upload(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool,
    ) -> EditResult:
        """Async version of `_edit_via_upload`, delegating to `aexecute` and `aupload_files`. / `_edit_via_upload` 的异步版本，委托给 `aexecute` 与 `aupload_files`。"""
        uid = base64.b32encode(os.urandom(10)).decode("ascii").lower()
        old_tmp = f"/tmp/.zharness_edit_{uid}_old"
        new_tmp = f"/tmp/.zharness_edit_{uid}_new"

        resps = await self.aupload_files(
            [
                (old_tmp, old_string.encode("utf-8")),
                (new_tmp, new_string.encode("utf-8")),
            ]
        )
        if len(resps) < 2:
            return EditResult(
                error=f"Error editing file '{file_path}': upload returned no response"
            )
        for r in resps:
            if r.error:
                return EditResult(error=f"Error editing file '{file_path}': {r.error}")

        cmd = _build_edit_tmpfile_cmd(
            file_path, old_tmp, new_tmp, replace_all=replace_all
        )
        result = await self.aexecute(cmd)
        output = result.output.rstrip()

        try:
            data = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            cleanup = await self.aexecute(
                f"rm -f {shlex.quote(old_tmp)} {shlex.quote(new_tmp)}"
            )
            if cleanup.exit_code != 0:
                logger.warning(
                    "Failed to clean up temp files for edit %s: %s",
                    file_path,
                    cleanup.output[:200],
                )
            detail = output[:200] if output else "(empty)"
            return EditResult(
                error=f"Error editing file '{file_path}': unexpected server response: {detail}"
            )

        if not isinstance(data, dict):
            detail = output[:200] if output else "(empty)"
            return EditResult(
                error=f"Error editing file '{file_path}': unexpected server response: {detail}"
            )

        if "error" in data:
            return _map_edit_error(data["error"], file_path, old_string)

        return EditResult(path=file_path, occurrences=data.get("count", 1))

    def delete(self, file_path: str) -> DeleteResult:
        """Delete a file or directory from the sandbox via a server-side `rm`.

        Runs `test -e || test -L` first: a path that does not exist (and is not
        a broken symlink) returns a not-found error, matching the contract of
        `LocalSandbox`. Because a shell `test` has no
        error channel, a non-zero probe conflates "absent" with "unstattable"
        (e.g. an unsearchable parent directory); an unknown exit code is not
        treated as absent and falls through to the delete.

        Uses `rm -rf`, so directories are removed recursively along with their
        contents. A recursive delete may remove some entries before failing
        partway; a non-zero `rm` exit (e.g. a permission error) is reported as
        a failure.

        Args:
            file_path: Absolute path to the file or directory to delete.

        Returns:
            `DeleteResult` with the deleted path on success, or an error if the
                path does not exist or the deletion command fails.

        通过服务端 `rm` 从沙箱中删除文件或目录。

        先运行 `test -e || test -L`：不存在的路径（且不是损坏的符号链接）返回
        not-found 错误，与 `LocalSandbox` 的契约一致。由于 shell `test` 没有错误
        通道，非零探测会把“不存在”与“无法 stat”（例如父目录不可搜索）混为一谈；
        未知退出码不当作不存在，而是继续执行删除。

        使用 `rm -rf`，因此目录连同其内容被递归删除。递归删除可能在部分条目删除
        后才中途失败；非零 `rm` 退出（例如权限错误）被报告为失败。

        参数：
            file_path: 要删除文件或目录的绝对路径。

        返回：
            成功时带已删除路径的 `DeleteResult`；若路径不存在或删除命令失败则返回错误。
        """
        # `shlex.quote` only neutralizes shell metacharacters so the path is
        # passed to `rm` as a single literal argument. It is NOT a security
        # boundary: it does not confine the deletion to any sandbox root or
        # block traversal. Whatever the sandbox shell can reach, this can delete.
        # `shlex.quote` 只中和 shell 元字符，使路径作为单个字面参数传给 `rm`。
        # 它并非安全边界：不把删除限制在某个沙箱根内，也不阻止路径穿越。
        # 沙箱 shell 能触达的任何内容，此操作都能删除。
        quoted = shlex.quote(file_path)
        exists = self.execute(f"test -e {quoted} || test -L {quoted}")
        # `exit_code` may be None when the backend cannot determine a status;
        # only a definite non-zero means the path is absent. Treating None as
        # not-found would fabricate a diagnosis and skip the delete, so fall
        # through to `rm` on an unknown probe result (matches the `rm` check
        # below and `_parse_grep_output`, which both guard `is not None`).
        # 当后端无法确定状态时 `exit_code` 可能为 None；只有确定非零才表示路径
        # 不存在。把 None 当作 not-found 会捏造诊断并跳过删除，因此在未知探测结果
        # 时继续执行 `rm`（与下方 `rm` 检查及 `_parse_grep_output` 一致，
        # 二者都用 `is not None` 守卫）。
        if exists.exit_code is not None and exists.exit_code != 0:
            return DeleteResult(error=f"Error: '{file_path}' not found")
        result = self.execute(f"rm -rf {quoted}")

        if result.exit_code == 0:
            return DeleteResult(path=file_path)

        return DeleteResult(
            error=f"Error deleting file '{file_path}': {result.output.strip() or 'unknown error'}"
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Search file contents for a literal string using `grep -F`.

        Args:
            pattern: Literal string to search for (not a regex).
            path: Directory or file to search in.

                Defaults to `"."`.
            glob: Optional glob to restrict the search. Patterns without a
                `/` (e.g. `'*.py'`) match basenames at any depth via
                `grep --include`; patterns containing a `/` (e.g.
                `'src/**/*.py'`) match the search-root-relative path via an
                in-process Python glob.
            max_count: Optional total cap on returned matches across all files.
                `None` returns every match; an int stops the search once the cap
                is reached and flags the result with `truncated=True`.

        Returns:
            `GrepResult` with a list of `GrepMatch` dicts, or `error` on failure.

        使用 `grep -F` 搜索文件内容中的字面字符串。

        参数：
            pattern: 要搜索的字面字符串（非正则）。
            path: 要搜索的目录或文件。

                默认为 `"."`。
            glob: 可选，用于限制搜索范围的 glob。不含 `/` 的模式（如 `'*.py'`）
                通过 `grep --include` 匹配任意深度的 basename；含 `/` 的模式
                （如 `'src/**/*.py'`）通过进程内 Python glob 匹配相对搜索根的路径。
            max_count: 可选，所有文件返回匹配总数的上限。`None` 返回全部匹配；
                整数则在达到上限时停止搜索并把结果标记为 `truncated=True`。

        返回：
            带 `GrepMatch` dict 列表的 `GrepResult`，失败时带 `error`。
        """
        result = self.execute(_build_grep_cmd(pattern, path, glob, max_count))
        return _parse_grep_output(result, path, max_count)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """Async version of `grep`, delegating to `aexecute` with timeout guard. / `grep` 的异步版本，委托给带超时防护的 `aexecute`。"""
        try:
            result = await asyncio.wait_for(
                self.aexecute(_build_grep_cmd(pattern, path, glob, max_count)),
                timeout=ASYNC_GREP_TIMEOUT,
            )
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
        return _parse_grep_output(result, path, max_count)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Structured glob matching returning `GlobResult`.

        Returned paths are absolute (see `_absolutize_glob_path`), which
        `_check_fs_permission` relies on to apply `deny` rules.

        结构化 glob 匹配，返回 `GlobResult`。

        返回的路径是绝对的（参见 `_absolutize_glob_path`），`_check_fs_permission`
        依赖这一点来应用 `deny` 规则。
        """
        search_path = _glob_search_root(path)
        result = self.execute(_build_glob_cmd(pattern, search_path))
        return _parse_glob_output(result, search_path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """Async version of `glob`, delegating to `aexecute`.

        Bounded by `ASYNC_GLOB_TIMEOUT`: the remote script's own `TIME_BUDGET`
        covers only the walk, so without an outer timeout a wedged sandbox
        hangs the caller with no upper bound.

        `glob` 的异步版本，委托给 `aexecute`。

        受 `ASYNC_GLOB_TIMEOUT` 约束：远端脚本自身的 `TIME_BUDGET` 只覆盖遍历，
        因此若没有外层超时，卡死的沙箱会无限期挂起调用方。
        """
        search_path = _glob_search_root(path)
        try:
            result = await asyncio.wait_for(
                self.aexecute(_build_glob_cmd(pattern, search_path)),
                timeout=ASYNC_GLOB_TIMEOUT,
            )
        except TimeoutError:
            logger.warning(
                "aglob timed out after %ds (pattern=%r, path=%r)",
                ASYNC_GLOB_TIMEOUT,
                pattern,
                search_path,
            )
            return GlobResult(
                error=f"Error: glob timed out after {ASYNC_GLOB_TIMEOUT}s. Try a more specific pattern or a narrower path.",
            )
        return _parse_glob_output(result, search_path)

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier for the sandbox backend. / 沙箱后端的唯一标识符。"""

    @abstractmethod
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload multiple files to the sandbox.

        Implementations must support partial success - catch exceptions per-file
        and return errors in `FileUploadResponse` objects rather than raising.

        Upload files is responsible for ensuring that the parent path exists
        (if user permissions allow the user to write to the given directory)

        向沙箱上传多个文件。

        实现必须支持部分成功——逐文件捕获异常，并把错误放进 `FileUploadResponse`
        对象而非抛出。

        上传文件负责确保父路径存在（若用户权限允许写入给定目录）。
        """

    @abstractmethod
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download multiple files from the sandbox.

        Implementations must support partial success - catch exceptions per-file
        and return errors in `FileDownloadResponse` objects rather than raising.

        从沙箱下载多个文件。

        实现必须支持部分成功——逐文件捕获异常，并把错误放进 `FileDownloadResponse`
        对象而非抛出。
        """
