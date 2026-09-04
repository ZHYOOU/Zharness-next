"""Skill system: parsing, storage, catalog, and deferred discovery. / 技能系统：解析、存储、目录与延迟发现。"""

from __future__ import annotations

from .catalog import SkillCatalog
from .describe import build_describe_skill_tool, get_skill_index_prompt_section
from .effective import effective_skills_root, ensure_effective_skills_root
from .state import (
    STATE_FILE_NAME,
    SkillState,
    SkillStateError,
    default_state_path,
    disable_skill,
    enable_skill,
)
from .storage import LocalSkillStorage, skills_root_path
from .types import SecretRequirement, Skill, SkillCategory
from .validation import _validate_skill_frontmatter, validate_skill_name

__all__ = [
    "LocalSkillStorage",
    "STATE_FILE_NAME",
    "SecretRequirement",
    "Skill",
    "SkillCatalog",
    "SkillCategory",
    "SkillState",
    "SkillStateError",
    "_validate_skill_frontmatter",
    "build_describe_skill_tool",
    "default_state_path",
    "disable_skill",
    "effective_skills_root",
    "enable_skill",
    "ensure_effective_skills_root",
    "get_skill_index_prompt_section",
    "skills_root_path",
    "validate_skill_name",
]
