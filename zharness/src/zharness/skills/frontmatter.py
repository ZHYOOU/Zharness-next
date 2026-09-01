"""Shared SKILL.md frontmatter parsing helpers.

The runtime parser and the install-time validator both use this module as the
schema source for ZHarness SKILL.md metadata.

共享的 SKILL.md frontmatter 解析辅助。运行时解析器与安装期校验器都以此模块作为
ZHarness SKILL.md 元数据的 schema 来源。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

ALLOWED_FRONTMATTER_PROPERTIES = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "required-secrets",
    "secrets-autonomous",
    "metadata",
    "compatibility",
    "version",
    "author",
}

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass(frozen=True)
class SkillMarkdownParts:
    """Parsed pieces of a SKILL.md document. / SKILL.md 文档的解析片段。"""

    metadata: dict[str, Any]
    frontmatter_text: str
    body: str


def split_skill_markdown(content: str) -> tuple[SkillMarkdownParts | None, str | None]:
    """Split a SKILL.md document into frontmatter and body.

    Returns ``(parts, None)`` on success and ``(None, message)`` on failure.

    将 SKILL.md 文档拆分为 frontmatter 与正文。成功时返回 ``(parts, None)``，
    失败时返回 ``(None, message)``。
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return None, "No YAML frontmatter found"

    frontmatter_text = match.group(1)
    try:
        metadata = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        return None, f"Invalid YAML in frontmatter: {exc}"

    if not isinstance(metadata, dict):
        return None, "Frontmatter must be a YAML dictionary"

    # YAML permits non-string keys, but downstream validation expects strings.
    #
    # YAML 允许非字符串键，但下游校验期望键为字符串。
    metadata = {str(key): value for key, value in metadata.items()}

    return (
        SkillMarkdownParts(
            metadata=metadata,
            frontmatter_text=frontmatter_text,
            body=content[match.end() :],
        ),
        None,
    )