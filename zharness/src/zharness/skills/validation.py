"""Skill frontmatter validation utilities.

Pure-logic validation of SKILL.md frontmatter with no HTTP dependencies.

技能 frontmatter 校验工具。对 SKILL.md frontmatter 做纯逻辑校验，无 HTTP 依赖。
"""

from __future__ import annotations

import re
from pathlib import Path

from zharness.skills.constants import SKILL_MD_FILE
from zharness.skills.frontmatter import (
    ALLOWED_FRONTMATTER_PROPERTIES,
    split_skill_markdown,
)
from zharness.skills.parser import parse_allowed_tools

# Hyphen-case skill name: lowercase letters, digits, and single hyphens. / 连字符命名技能名：小写字母、数字与单个连字符。
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def validate_skill_name(name: str) -> str:
    """Validate and normalise a skill name; return the normalised form. / 校验并规范化技能名；返回规范化形式。"""
    normalized = name.strip()
    if not _SKILL_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Skill name must be hyphen-case using lowercase letters, digits, and hyphens only."
        )
    if len(normalized) > 64:
        raise ValueError("Skill name must be 64 characters or fewer.")
    return normalized


def _validate_skill_frontmatter(skill_dir: Path) -> tuple[bool, str, str | None]:
    """Validate a skill directory's SKILL.md frontmatter.

    Args:
        skill_dir: Path to the skill directory containing SKILL.md.

    Returns:
        Tuple of ``(is_valid, message, skill_name)``.

    校验技能目录的 SKILL.md frontmatter。

    Args:
        skill_dir: 包含 SKILL.md 的技能目录路径。

    Returns:
        ``(is_valid, message, skill_name)`` 三元组。
    """
    skill_md = skill_dir / SKILL_MD_FILE
    if not skill_md.exists():
        return False, f"{SKILL_MD_FILE} not found", None

    content = skill_md.read_text(encoding="utf-8")
    parts, error = split_skill_markdown(content)
    if error:
        return False, error, None
    if parts is None:
        return False, "Invalid frontmatter format", None
    frontmatter = parts.metadata

    unexpected_keys = set(frontmatter.keys()) - ALLOWED_FRONTMATTER_PROPERTIES
    if unexpected_keys:
        return (
            False,
            f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}",
            None,
        )

    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter", None
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter", None

    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}", None
    name = name.strip()
    if not name:
        return False, "Name cannot be empty", None

    try:
        validate_skill_name(name)
    except ValueError as exc:
        return False, str(exc), None

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return (
            False,
            f"Description must be a string, got {type(description).__name__}",
            None,
        )
    description = description.strip()
    if not description:
        return False, "Description cannot be empty", None
    if "<" in description or ">" in description:
        return False, "Description cannot contain angle brackets (< or >)", None
    if len(description) > 1024:
        return (
            False,
            f"Description is too long ({len(description)} characters). Maximum is 1024 characters.",
            None,
        )

    try:
        parse_allowed_tools(frontmatter.get("allowed-tools"), skill_md)
    except ValueError as exc:
        return False, str(exc).replace(str(skill_md), SKILL_MD_FILE), None

    required_secrets = frontmatter.get("required-secrets")
    if required_secrets is not None and not isinstance(required_secrets, list):
        return False, f"required-secrets in {SKILL_MD_FILE} must be a list", None

    secrets_autonomous = frontmatter.get("secrets-autonomous")
    if secrets_autonomous is not None and not isinstance(secrets_autonomous, bool):
        return False, f"secrets-autonomous in {SKILL_MD_FILE} must be a boolean", None

    return True, "Skill is valid!", name
