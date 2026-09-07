"""Deterministic Chinese and code-aware lexical normalization. / 确定性的中文与代码感知词法规范化。"""

from __future__ import annotations

import re

_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]*|\d+(?:\.\d+)*|[\u3400-\u9fff]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_CODE_SEPARATOR = re.compile(r"[_.:/-]+")
_CJK_PATTERN = re.compile(r"^[\u3400-\u9fff]+$")


def lexicalize(text: str) -> str:
    """Return space-separated lexemes for PostgreSQL simple text search.

    返回用于 PostgreSQL simple 全文检索的空格分隔词元。
    """
    lexemes: list[str] = []
    for match in _TOKEN_PATTERN.finditer(text):
        token = match.group(0)
        if _CJK_PATTERN.fullmatch(token):
            lexemes.extend(_cjk_lexemes(token))
        else:
            lexemes.extend(_code_lexemes(token))
    return " ".join(_deduplicate(lexemes))


def _cjk_lexemes(token: str) -> list[str]:
    """Split a CJK run into stable unigrams and bigrams. / 将连续中日韩文字切为稳定的单字与双字词元。"""
    if len(token) == 1:
        return [token]
    return [*token, *(token[index : index + 2] for index in range(len(token) - 1))]


def _code_lexemes(token: str) -> list[str]:
    """Keep an identifier and add separator/camel-case components. / 保留标识符并添加分隔符与驼峰组成部分。"""
    lowered = token.lower()
    parts: list[str] = [lowered]
    for separated in _CODE_SEPARATOR.split(token):
        if not separated:
            continue
        parts.extend(part.lower() for part in _CAMEL_BOUNDARY.split(separated) if part)
    return parts


def _deduplicate(values: list[str]) -> list[str]:
    """Deduplicate lexemes while preserving order. / 在保持顺序的同时对词元去重。"""
    return list(dict.fromkeys(value for value in values if value))
