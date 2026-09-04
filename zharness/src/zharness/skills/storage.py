"""Skill storage — discovery of SKILL.md packages on the local filesystem. / 技能存储——在本地文件系统上发现 SKILL.md 技能包。"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from pathlib import Path

from zharness.config.loader import get_settings
from zharness.host.paths import WorkspacePathError, zharness_home
from zharness.skills.constants import (
    DEFAULT_SKILLS_CONTAINER_PATH,
    SKILL_MD_FILE,
)
from zharness.skills.parser import parse_skill_file
from zharness.skills.state import SkillState
from zharness.skills.types import Skill, SkillCategory

logger = logging.getLogger(__name__)


def _repo_root_skills() -> Path | None:
    """Return the checked-in ``skills/`` directory when running from the source tree. / 从源码树运行时返回仓库内的 ``skills/`` 目录。"""
    candidate = Path(__file__).resolve().parents[4] / "skills"
    return candidate if candidate.is_dir() else None


def skills_root_path() -> Path:
    """Resolve the skills directory.

    Resolution order:

    1. The ``ZHARNESS_SKILLS_PATH`` environment variable or the ``skills.path``
       YAML setting.
    2. ``<ZHARNESS_HOME>/skills`` when it exists.
    3. The checked-in repository ``skills/`` directory (source-tree fallback).
    4. ``<ZHARNESS_HOME>/skills`` (may not exist yet).

    The returned directory may not exist yet; callers that need an existing
    directory should check ``is_dir()``.

    解析技能目录。

    解析顺序：

    1. ``ZHARNESS_SKILLS_PATH`` 环境变量或 ``skills.path`` YAML 配置。
    2. 存在时的 ``<ZHARNESS_HOME>/skills``。
    3. 仓库内检入的 ``skills/`` 目录（源码树回退）。
    4. ``<ZHARNESS_HOME>/skills``（可能尚不存在）。

    返回的目录可能尚不存在；需要已有目录的调用方应检查 ``is_dir()``。
    """
    configured_path = get_settings().skills.path
    if configured_path:
        try:
            return Path(configured_path).expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise WorkspacePathError("Could not resolve skills path") from exc

    home_skills = zharness_home() / "skills"
    if home_skills.is_dir():
        return home_skills

    repo_skills = _repo_root_skills()
    if repo_skills is not None:
        return repo_skills

    return home_skills


class LocalSkillStorage:
    """Discover skills under a local skills directory.

    Layout::

        <root>/public/<name>/SKILL.md
        <root>/user/<name>/SKILL.md

    在本地技能目录下发现技能。

    目录结构：:

        <root>/public/<name>/SKILL.md
        <root>/user/<name>/SKILL.md
    """

    def __init__(
        self,
        host_path: str | Path | None = None,
        *,
        container_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
        state: SkillState | None = None,
    ) -> None:
        self._host_root = (
            Path(host_path).expanduser().resolve(strict=False)
            if host_path is not None
            else skills_root_path()
        )
        self._container_root = container_path
        self._state = state

    def get_skills_root_path(self) -> Path:
        """Absolute host path to the skills root, used for sandbox mounts. / 技能根目录的绝对 host 路径，用于沙箱挂载。"""
        return self._host_root

    def get_container_root(self) -> str:
        """Container path where skills are mounted in the sandbox. / 技能在沙箱中的挂载容器路径。"""
        return self._container_root

    def _iter_skill_files(self) -> list[tuple[SkillCategory, Path, Path]]:
        """Yield ``(category, category_root, skill_md_path)`` for every SKILL.md. / 为每个 SKILL.md 产出 ``(category, category_root, skill_md_path)``。"""
        found: list[tuple[SkillCategory, Path, Path]] = []
        if not self._host_root.exists():
            return found
        for category in SkillCategory:
            category_path = self._host_root / category.value
            if (
                not category_path.exists()
                or not category_path.is_dir()
                or category_path.is_symlink()
            ):
                continue
            for current_root, dir_names, file_names in os.walk(category_path):
                dir_names[:] = sorted(
                    name for name in dir_names if not name.startswith(".")
                )
                if SKILL_MD_FILE not in file_names:
                    continue
                # A directory containing SKILL.md is a package boundary. Nested
                # SKILL.md files belong to that package's supporting resources,
                # not to the runtime registry. Namespace directories without
                # SKILL.md still recurse, preserving public/team/helper layouts.
                #
                # 包含 SKILL.md 的目录是一个技能包边界。嵌套的 SKILL.md 属于该包的配套资源，
                # 不属于运行时注册表。没有 SKILL.md 的命名空间目录仍会继续递归，
                # 以保留 public/team/helper 这样的布局。
                dir_names.clear()
                found.append(
                    (category, category_path, Path(current_root) / SKILL_MD_FILE)
                )
        return found

    def _skill_state(self) -> SkillState:
        """Return the enablement state, resolving the default path lazily.

        The default state file lives under ZHarness home and is shared across
        all storages; the default path is resolved on first use so a later
        ``ZHARNESS_HOME`` change still applies.

        返回启停状态，延迟解析默认路径。默认状态文件位于 ZHarness home 下，在所有
        storage 实例间共享；默认路径在首次使用时解析，以便之后的 ``ZHARNESS_HOME``
        变更仍然生效。
        """
        if self._state is None:
            self._state = SkillState()
        return self._state

    def load_skills(self, *, enabled_only: bool = False) -> list[Skill]:
        """Discover all skills, deduplicated by name and sorted.

        Each skill's ``enabled`` flag reflects the dynamic state store, so
        disabled skills are still returned (with ``enabled=False``) unless
        ``enabled_only`` is set.

        发现所有技能，按名称去重并排序。每个技能的 ``enabled`` 标志反映动态状态存储，
        因此被禁用的技能仍会返回（``enabled=False``），除非设置 ``enabled_only``。
        """
        enabled_map = self._skill_state().load()
        skills_by_name: dict[str, Skill] = {}
        for category, category_root, md_path in self._iter_skill_files():
            skill = parse_skill_file(
                md_path,
                category=category,
                relative_path=md_path.parent.relative_to(category_root),
            )
            if skill:
                skills_by_name[skill.name] = replace(
                    skill, enabled=enabled_map.get(skill.name, True)
                )

        skills = list(skills_by_name.values())
        if enabled_only:
            skills = [s for s in skills if s.enabled]
        skills.sort(key=lambda s: s.name)
        return skills
