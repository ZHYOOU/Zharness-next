"""Core skill data types. / 技能核心数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from zharness.skills.constants import DEFAULT_SKILLS_CONTAINER_PATH, SKILL_MD_FILE


class SkillCategory(StrEnum):
    """Source category for a skill.

    - ``PUBLIC``: built-in skill bundled with the platform, read-only.
    - ``USER``: user-authored skill that can be edited or deleted.

    技能的来源类别。

    - ``PUBLIC``：平台内置技能，只读。
    - ``USER``：用户自建技能，可编辑或删除。
    """

    PUBLIC = "public"
    USER = "user"


@dataclass(frozen=True)
class SecretRequirement:
    """A secret a skill declares it needs (``required-secrets`` frontmatter).

    ``name`` is both the key looked up in the request context and the
    environment-variable name injected into the skill's sandbox subprocess when
    the skill is activated.

    技能声明需要的密钥（``required-secrets`` frontmatter）。``name`` 既是在请求上下文中
    查找的键，也是技能激活时注入技能沙箱子进程的环境变量名。
    """

    name: str
    optional: bool = False


@dataclass(frozen=True)
class Skill:
    """A skill with its metadata and on-disk location. / 携带元数据与磁盘位置的技能。"""

    name: str
    description: str
    license: str | None
    skill_dir: Path
    skill_file: Path
    relative_path: Path  # Relative path from the category root to the skill dir. / 从类别根目录到技能目录的相对路径。
    category: SkillCategory
    allowed_tools: tuple[str, ...] | None = None
    enabled: bool = False  # Whether this skill is enabled. / 该技能是否启用。
    required_secrets: tuple[SecretRequirement, ...] = field(default_factory=tuple)
    secrets_autonomous: bool = True

    @property
    def skill_path(self) -> str:
        """Relative path from the category root (skills/{category}) to the skill dir. / 从类别根目录（skills/{category}）到技能目录的相对路径。"""
        path = self.relative_path.as_posix()
        return "" if path == "." else path

    def get_container_path(
        self,
        container_base_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
    ) -> str:
        """Full path to this skill's directory inside the sandbox. / 该技能目录在沙箱内的完整路径。"""
        category_base = f"{container_base_path}/{self.category}"
        skill_path = self.skill_path
        if skill_path:
            return f"{category_base}/{skill_path}"
        return category_base

    def get_container_file_path(
        self,
        container_base_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
    ) -> str:
        """Full path to this skill's ``SKILL.md`` inside the sandbox. / 该技能 ``SKILL.md`` 在沙箱内的完整路径。"""
        return f"{self.get_container_path(container_base_path)}/{SKILL_MD_FILE}"

    def __repr__(self) -> str:
        return (
            f"Skill(name={self.name!r}, description={self.description!r}, "
            f"category={self.category!r})"
        )