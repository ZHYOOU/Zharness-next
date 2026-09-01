"""SKILL.md frontmatter parsing into :class:`Skill` objects. / 将 SKILL.md frontmatter 解析为 :class:`Skill` 对象。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from zharness.skills.frontmatter import split_skill_markdown
from zharness.skills.types import SKILL_MD_FILE, SecretRequirement, Skill, SkillCategory

logger = logging.getLogger(__name__)

# Valid POSIX environment-variable name. / 合法的 POSIX 环境变量名。
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_allowed_tools(raw: object, skill_file: Path) -> tuple[str, ...] | None:
    """Parse the optional ``allowed-tools`` frontmatter field.

    Returns ``None`` when the field is omitted, a tuple when the field is a
    YAML sequence of strings (including an empty tuple for explicit no-tool
    skills), and raises ``ValueError`` for malformed values.

    解析可选的 ``allowed-tools`` frontmatter 字段。字段省略时返回 ``None``；
    字段为字符串的 YAML 序列时返回元组（无工具技能返回空元组）；格式错误时抛出 ``ValueError``。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        # ValueError is the public contract: callers catch it to reject a skill
        # without failing the whole parse.
        #
        # ValueError 是公开契约：调用方捕获它以拒绝技能，而不会导致整体解析失败。
        raise ValueError(f"allowed-tools in {skill_file} must be a list of strings")  # noqa: TRY004

    allowed_tools: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"allowed-tools in {skill_file} must contain only strings")  # noqa: TRY004
        tool_name = item.strip()
        if not tool_name:
            raise ValueError(
                f"allowed-tools in {skill_file} cannot contain empty tool names"
            )
        allowed_tools.append(tool_name)
    return tuple(allowed_tools)


def parse_required_secrets(
    raw: object, skill_file: Path
) -> tuple[SecretRequirement, ...]:
    """Parse the optional ``required-secrets`` frontmatter field.

    Accepts a YAML sequence whose items are either a string (the secret / env
    variable name) or a mapping (``{name, optional}``). Returns an empty tuple
    when the field is omitted. Malformed entries are dropped with a warning so
    one bad declaration does not invalidate the whole skill.

    解析可选的 ``required-secrets`` frontmatter 字段。接受字符串（密钥/环境变量名）或
    映射（``{name, optional}``）组成的 YAML 序列。字段省略时返回空元组。格式错误的条目会被
    警告并丢弃，避免单个错误声明使整个技能失效。
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        # ValueError is the public contract; see parse_allowed_tools. / ValueError 是公开契约；参见 parse_allowed_tools。
        raise ValueError(f"required-secrets in {skill_file} must be a list")  # noqa: TRY004

    secrets: list[SecretRequirement] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            name, optional = item.strip(), False
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            optional = bool(item.get("optional", False))
        else:
            logger.warning(
                "Ignoring malformed required-secrets entry in %s: %r", skill_file, item
            )
            continue

        if not _ENV_VAR_NAME_RE.match(name):
            logger.warning(
                "Ignoring required-secrets entry with invalid env var name in %s: %r",
                skill_file,
                name,
            )
            continue
        if name in seen:
            continue
        seen.add(name)
        secrets.append(SecretRequirement(name=name, optional=optional))
    return tuple(secrets)


def parse_secrets_autonomous(raw: object, skill_file: Path) -> bool:
    """Parse the optional ``secrets-autonomous`` frontmatter field.

    ``True`` (the default) lets declared secrets bind while the skill is
    in-context via an autonomous model load; ``False`` restricts binding to
    explicit slash activation. A malformed value fails closed to ``False``.

    解析可选的 ``secrets-autonomous`` frontmatter 字段。``True``（默认）允许声明的密钥
    在技能处于上下文（自主模型加载）时绑定；``False`` 将绑定限制为显式斜杠激活。
    格式错误的值以 ``False`` 关闭（更安全的方向）。
    """
    if raw is None:
        return True
    if isinstance(raw, bool):
        return raw
    logger.warning(
        "Ignoring malformed secrets-autonomous value in %s: %r", skill_file, raw
    )
    return False


def parse_skill_file(
    skill_file: Path,
    category: SkillCategory,
    relative_path: Path | None = None,
) -> Skill | None:
    """Parse a SKILL.md file and extract its metadata.

    Args:
        skill_file: Path to the SKILL.md file.
        category: Category of the skill.
        relative_path: Path from the category root to the skill directory.
            Defaults to the skill directory name when omitted.

    Returns:
        A :class:`Skill` if parsing succeeds, ``None`` otherwise.

    解析 SKILL.md 文件并提取其元数据。

    Args:
        skill_file: SKILL.md 文件的路径。
        category: 技能的类别。
        relative_path: 从类别根目录到技能目录的路径。省略时默认为技能目录名。

    Returns:
        解析成功返回 :class:`Skill`，否则返回 ``None``。
    """
    if not skill_file.exists() or skill_file.name != SKILL_MD_FILE:
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")
        parts, error = split_skill_markdown(content)
        if error:
            logger.error("Invalid SKILL.md front-matter in %s: %s", skill_file, error)
            return None
        if parts is None:
            return None
        metadata = parts.metadata

        name = metadata.get("name")
        description = metadata.get("description")
        if not name or not isinstance(name, str):
            return None
        if not description or not isinstance(description, str):
            return None

        name = name.strip()
        description = description.strip()
        if not name or not description:
            return None

        license_text = metadata.get("license")
        if license_text is not None:
            license_text = str(license_text).strip() or None

        try:
            allowed_tools = parse_allowed_tools(
                metadata.get("allowed-tools"), skill_file
            )
        except ValueError as exc:
            logger.error("Invalid allowed-tools in %s: %s", skill_file, exc)
            return None

        try:
            required_secrets = parse_required_secrets(
                metadata.get("required-secrets"), skill_file
            )
        except ValueError as exc:
            logger.error("Invalid required-secrets in %s: %s", skill_file, exc)
            return None

        secrets_autonomous = parse_secrets_autonomous(
            metadata.get("secrets-autonomous"), skill_file
        )

        return Skill(
            name=name,
            description=description,
            license=license_text,
            skill_dir=skill_file.parent,
            skill_file=skill_file,
            relative_path=relative_path or Path(skill_file.parent.name),
            category=category,
            allowed_tools=allowed_tools,
            enabled=True,
            required_secrets=required_secrets,
            secrets_autonomous=secrets_autonomous,
        )

    except Exception:
        logger.exception("Unexpected error parsing skill file %s", skill_file)
        return None